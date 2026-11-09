"""ガンビットの条件・変型・優先行動の定義。カタログ全体やDBには依存しない."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from .catalog_identifiers import DefinitionId
from .trigger_variants import apply_trigger_patch
from .variant_sets import VariantCandidate, VariantSelection


def trigger_unlock_id(trigger_id: str, variant_id: str | None = None) -> str:
    """基本条件と変型の購入権を衝突しないIDにする."""
    return f"{trigger_id}:{variant_id}" if variant_id else trigger_id


MAX_TRIGGER_PERCENT = 100


class TriggerCondition(BaseModel):
    """管理定義とプレイヤー設定で共有する条件パラメータ."""

    condition: Literal[
        "always",
        "hp_below",
        "hp_above",
        "hp_equal",
        "mp_below",
        "mp_above",
        "mp_equal",
        "enemy_count",
        "state",
        "stat",
    ] = "always"
    threshold: int = Field(default=30, ge=0, le=100000)
    stat: Literal[
        "hp",
        "mp",
        "attack",
        "defense",
        "special_attack",
        "special_defense",
        "speed",
    ] = "hp"
    stat_basis: Literal["current", "maximum", "percent"] = "current"
    comparison: Literal["above", "below", "equal", "lowest", "highest"] = "below"
    state_present: bool = True
    state_skill_id: DefinitionId | None = None

    @model_validator(mode="after")
    def valid_stat_condition(self):
        """割合・最大値はHP/MPだけに使い、割合は100%までとする."""
        if (
            self.condition == "stat"
            and self.stat not in {"hp", "mp"}
            and self.stat_basis != "current"
        ):
            msg = "現在値以外の判定はHP・MPだけに設定できます。"
            raise ValueError(msg)
        percent = self.condition.startswith(("hp_", "mp_")) or (
            self.condition == "stat"
            and self.stat_basis == "percent"
            and self.comparison not in {"lowest", "highest"}
        )
        if percent and self.threshold > MAX_TRIGGER_PERCENT:
            msg = "割合のしきい値は0〜100にしてください。"
            raise ValueError(msg)
        return self


def supported_trigger(value) -> bool:
    """保存境界では、現在の条件として実行できる定義だけを読み込む。."""
    return (
        isinstance(value, dict)
        and value.get("condition", "always")
        in TriggerCondition.model_fields["condition"].annotation.__args__
        and (value.get("condition") != "state" or bool(value.get("state_skill_id")))
    )


TriggerTarget = Literal["enemy", "self", "ally"]
TriggerVariantField = Literal[
    "target",
    "condition",
    "threshold",
    "state_skill_id",
    "state_present",
    "stat",
    "stat_basis",
    "comparison",
]


class TriggerVariantValue(VariantCandidate):
    """一つの条件項目を置き換える、副名付きの候補。."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=60)
    value: str | int | bool


class TriggerVariant(TriggerVariantValue):
    """親の変型を継承し、一項目か共有セットの候補を上書きする."""

    value: str | int | bool | None = None
    field: TriggerVariantField | None = None
    child_field: TriggerVariantField | None = None
    base_variant_id: DefinitionId | None = None
    variant_set_ids: list[DefinitionId] = Field(default_factory=list, max_length=8)
    variant_selections: list[VariantSelection] = Field(
        default_factory=list,
        max_length=8,
    )
    resolved_name: str | None = None
    resolved_values: dict | None = None
    effective_set_ids: list[DefinitionId] = Field(default_factory=list, max_length=8)

    @model_serializer(mode="wrap")
    def compact(self, handler):
        """個別の旧候補には不要な派生情報を追加しない."""
        values = handler(self)
        return {k: v for k, v in values.items() if v is not None and v != []}


class TriggerVariantSet(BaseModel):
    """複数のトリガーで共有する、一項目の変型候補."""

    model_config = ConfigDict(extra="forbid")
    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    field: TriggerVariantField = "threshold"
    values: list[TriggerVariantValue] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def valid_values(self):
        """未使用のセットも値の型と範囲を検査する."""
        if len({v.id for v in self.values}) != len(self.values):
            msg = "ガンビット変型セットの候補IDが重複しています。"
            raise ValueError(msg)
        for candidate in self.values:
            expected = {"threshold": int, "state_present": bool}.get(self.field, str)
            if type(candidate.value) is not expected:
                msg = "ガンビット変型セットの値が不正です。"
                raise ValueError(msg)
            TriggerCondition.model_validate({self.field: candidate.value})
            if self.field == "target" and candidate.value not in {
                "enemy",
                "self",
                "ally",
            }:
                msg = "ガンビット変型セットの対象が不正です。"
                raise ValueError(msg)
        return self


class TriggerDefinition(TriggerCondition):
    """管理者が公開する条件テンプレート。任意コードは実行しない."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    # None preserves old definitions whose condition and action targets differed.
    target: TriggerTarget | None = None
    variant_field: TriggerVariantField = "threshold"
    variants: list[TriggerVariant] = Field(default_factory=list, max_length=200)
    variant_set_id: DefinitionId | None = None
    variant_set_ids: list[DefinitionId] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def valid_variants(self):
        """未選択の候補も通常の条件と同じ型・範囲で検査する。."""
        if self.condition == "state" and not self.state_skill_id:
            msg = "状態判定には状態系スキルを選んでください。"
            raise ValueError(msg)
        if len({v.id for v in self.variants}) != len(self.variants):
            msg = "トリガーの変型IDが重複しています。"
            raise ValueError(msg)
        for variant in self.variants:
            if variant.resolved_values is not None:
                TriggerCondition.model_validate(variant.resolved_values)
                if variant.resolved_values.get("target") not in {
                    None,
                    "enemy",
                    "self",
                    "ally",
                } or not supported_trigger(variant.resolved_values):
                    msg = "変型の条件または対象が不正です。"
                    raise ValueError(msg)
                continue
            value = variant.value
            field = variant.field or self.variant_field
            expected = {"threshold": int, "state_present": bool}.get(field, str)
            if type(value) is not expected:
                msg = "トリガーの変型の値が不正です。"
                raise ValueError(msg)
            TriggerCondition.model_validate(self.variant(variant.id).model_dump())
            if not supported_trigger(self.variant(variant.id).model_dump()):
                msg_0 = "状態判定には状態系スキルを選んでください。"
                raise ValueError(msg_0)
            if field == "target" and value not in {"enemy", "self", "ally"}:
                msg = "トリガーの対象が不正です。"
                raise ValueError(msg)
        return self

    def variant(self, variant_id: str | None):
        """候補を解決し、削除された参照は使用不能として扱う。."""
        if variant_id is None:
            return self
        candidate = next((v for v in self.variants if v.id == variant_id), None)
        if not candidate:
            return None
        patch = (
            candidate.resolved_values
            if candidate.resolved_values is not None
            else apply_trigger_patch(
                self.model_dump(),
                {candidate.field or self.variant_field: candidate.value},
            )
        )
        return self.model_copy(
            update={
                k: v
                for k, v in patch.items()
                if k in {*TriggerCondition.model_fields, "target"}
            },
        )


def trigger_display_name(trigger, variant_id=None) -> str:
    """ショップと行動記録で、変型を含めて同じトリガー名を使う."""
    variant = next((v for v in trigger.variants if v.id == variant_id), None)
    return (
        f"{trigger.name}（{variant.resolved_name or variant.name}）"  # noqa: RUF001
        if variant
        else trigger.name
    )


class EnemyGambitRule(BaseModel):
    """敵が優先順に判定する、公開トリガーと使用スキルの組み合わせ。."""

    trigger_id: DefinitionId
    trigger_variant_id: DefinitionId | None = None
    skill_id: DefinitionId
    enabled: bool = True


class GambitSet(BaseModel):
    """複数の敵が共有できる、優先順つきの行動ルール."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    rules: list[EnemyGambitRule] = Field(default_factory=list, max_length=20)


def default_triggers() -> list[TriggerDefinition]:
    """単独条件のみ。AND/ORの式エディタは設けない."""
    return [
        TriggerDefinition(id=key, name=name, condition=key)
        for key, name in (
            ("always", "いつでも"),
            ("hp_below", "HPがn%未満"),
            ("mp_below", "MPがn%未満"),
            ("enemy_count", "敵がn体以上"),
        )
    ]
