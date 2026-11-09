"""効果テンプレートの定義と、継承・変型・スキル参照の解決."""

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_serializer,
    model_validator,
)

from .catalog_identifiers import DefinitionId
from .skill_definitions import (
    SKILL_IDENTITY_FIELDS,
    AilmentEffect,
    AttackEffect,
    BuffEffect,
    CounterResult,
    DebuffEffect,
    RecoveryEffect,
    ReducedAilmentResult,
    ReducedAttackResult,
    ReducedResult,
    ReducedTimedResult,
    UtilityEffect,
)
from .template_variant_sets import VariantField, VariantSet
from .variant_sets import VariantSelection, attached_sets, selected_values

TemplateOverride = VariantField


MAX_TEMPLATE_ROOT_NAME = 80
MAX_VARIANT_DEPTH = 16
VARIANT_OPEN = "\N{FULLWIDTH LEFT PARENTHESIS}"
VARIANT_CLOSE = "\N{FULLWIDTH RIGHT PARENTHESIS}"


class TemplateBase(BaseModel):
    """共通の効果設定の識別子と名前."""

    model_config = ConfigDict(extra="forbid")
    id: DefinitionId
    name: str = Field(min_length=1, max_length=1100)
    base_template_id: DefinitionId | None = None
    override_fields: list[TemplateOverride] = Field(default_factory=list, max_length=12)
    variant_axis: str | None = Field(default=None, max_length=40, pattern=r"^[a-z_]+$")
    variant_label: str | None = Field(default=None, min_length=1, max_length=60)
    variant_set_ids: list[DefinitionId] = Field(default_factory=list, max_length=8)
    variant_selections: list[VariantSelection] = Field(
        default_factory=list,
        max_length=8,
    )

    @model_validator(mode="after")
    def overrides_need_base(self):
        """独立定義には上書き指定を残さない。."""
        if self.override_fields and not self.base_template_id:
            msg = "上書き項目には共通設定の参照が必要です。"
            raise ValueError(msg)
        if self.variant_selections and (
            not self.base_template_id or self.override_fields
        ):
            msg = (
                "セットの組み合わせには親の参照が必要で、個別の上書きは併用できません。"
            )
            raise ValueError(msg)
        if not self.base_template_id and len(self.name) > MAX_TEMPLATE_ROOT_NAME:
            msg = "共通設定の名前は80文字以下で指定してください。"
            raise ValueError(msg)
        return self

    @model_serializer(mode="wrap")
    def without_target(self, handler):
        """対象の陣営はガンビットの責務。旧全体指定だけ対象数へ移す."""
        values = handler(self)
        for key in ("variant_axis", "variant_label"):
            if values.get(key) is None:
                values.pop(key, None)
        for key in ("variant_set_ids", "variant_selections"):
            if not values.get(key):
                values.pop(key, None)
        if not self.base_template_id:
            values.pop("base_template_id", None)
            values.pop("override_fields", None)
        if values.get("stat") not in {"damageDealt", "damageTaken"}:
            values.pop("affected_element", None)
        if values.pop("target", None) == "all_enemies":
            values["target_count"] = "all"
        return values


class AttackTemplate(TemplateBase, AttackEffect):
    """攻撃の共通設定."""


class RecoveryTemplate(TemplateBase, RecoveryEffect):
    """回復の共通設定."""


class BuffTemplate(TemplateBase, BuffEffect):
    """バフの共通設定."""


class DebuffTemplate(TemplateBase, DebuffEffect):
    """デバフの共通設定."""


class AilmentTemplate(TemplateBase, AilmentEffect):
    """状態異常の共通設定."""


class UtilityTemplate(TemplateBase, UtilityEffect):
    """補助効果の共通設定."""


SkillTemplate = Annotated[
    AttackTemplate
    | RecoveryTemplate
    | BuffTemplate
    | DebuffTemplate
    | AilmentTemplate
    | UtilityTemplate,
    Field(discriminator="effect"),
]
TEMPLATE_MODELS = {
    effect: model
    for model, effects in (
        (AttackTemplate, ("attack",)),
        (RecoveryTemplate, ("heal_hp", "heal_mp", "damage_mp")),
        (BuffTemplate, ("buff",)),
        (DebuffTemplate, ("debuff",)),
        (AilmentTemplate, ("ailment",)),
        (UtilityTemplate, ("recover_moves", "equip_attack")),
    )
    for effect in effects
}


def variant_values(base, template, fields):
    """種類変更時は無関係な項目を除去し、誤答効果も対応する形へ揃える。."""
    values = {**base, **{key: template[key] for key in fields}}
    model = TEMPLATE_MODELS.get(str(values.get("effect")))
    if not model:
        msg = "バリエーションの効果種別が不正です。"
        raise ValueError(msg)
    fields = [key for key in fields if key in model.model_fields]
    values = {key: value for key, value in values.items() if key in model.model_fields}
    if str(values.get("stat")) not in {"damageDealt", "damageTaken"}:
        values.pop("affected_element", None)
        fields = [key for key in fields if key != "affected_element"]
    wrong = values.get("on_wrong")
    if "on_wrong" not in fields and wrong and wrong.get("type") == "reduced":
        wrong_model = {
            "attack": ReducedAttackResult,
            "ailment": ReducedAilmentResult,
            "buff": ReducedTimedResult,
            "debuff": ReducedTimedResult,
        }.get(values["effect"], ReducedResult)
        values["on_wrong"] = wrong_model.model_validate({
            key: value
            for key, value in wrong.items()
            if key in wrong_model.model_fields
        }).model_dump(exclude_none=True)
    return {
        **values,
        "id": template.get("id"),
        "name": template.get("name"),
        "base_template_id": template["base_template_id"],
        "override_fields": fields,
        "variant_axis": template.get("variant_axis"),
        "variant_label": template.get("variant_label"),
        "variant_set_ids": template.get(
            "variant_set_ids",
            base.get("variant_set_ids", []),
        ),
        "variant_selections": template.get("variant_selections", []),
    }


def default_skill_templates() -> list[SkillTemplate]:
    """既存スキルへ勝手に適用せず、選択可能な雛形だけ提供する."""
    return [
        AttackTemplate(
            id="basic_physical",
            name="基本の物理攻撃",
            on_wrong=CounterResult(),
        ),
        AttackTemplate(
            id="basic_spell",
            name="初級単体魔法",
            damage_type="special",
            multiplier=1.5,
        ),
        RecoveryTemplate(
            id="basic_heal",
            name="初級HP割合回復",
            effect="heal_hp",
            amount=20,
            amount_mode="percent",
            on_wrong=ReducedResult(amount=10),
        ),
    ]


def read_effect_templates(raw_templates, variant_sets=None):
    """旧定義のMPを読み取った後、消費を持たないテンプレートへ移行する."""
    legacy_costs = {}
    if isinstance(raw_templates, list):
        raw_templates = [
            t.model_dump() if isinstance(t, BaseModel) else t for t in raw_templates
        ]
        legacy_costs = {
            t.get("id"): t.get("mp_cost", 0)
            for t in raw_templates
            if isinstance(t, dict) and isinstance(t.get("id"), str)
        }
        raw_templates = [
            {k: v for k, v in t.items() if k != "mp_cost"} if isinstance(t, dict) else t
            for t in raw_templates
        ]
        for template in raw_templates:
            if (
                isinstance(template, dict)
                and template.pop("target", None) == "all_enemies"
            ):
                template["target_count"] = "all"
    return resolve_template_variants(raw_templates, variant_sets), legacy_costs


def _variant_override_fields(template, allowed):
    """各段で、保存された差分のキーと値の存在を検査する."""
    fields = TypeAdapter(list[TemplateOverride]).validate_python(
        template.get("override_fields", []),
    )
    if len(set(fields)) != len(fields) or any(key not in template for key in fields):
        msg = "上書き項目が重複しているか、共通設定に適用できません。"
        raise ValueError(msg)
    if set(template) - allowed:
        msg = "バリエーションに未定義の項目があります。"
        raise ValueError(msg)
    return fields


def _variant_label(template, base_name):
    """旧形式の表示名も、既存値を変更せず副名として扱う."""
    name = template.get("name", "")
    prefix = base_name + VARIANT_OPEN
    return template.get("variant_label") or (
        name[len(prefix) : -1]
        if name.startswith(prefix) and name.endswith(VARIANT_CLOSE)
        else name
    )


def _template_entries(raw):
    """参照を辿る前にIDの型と一意性を検証する."""
    if not isinstance(raw, list) or any(not isinstance(t, dict) for t in raw):
        return None
    if any(
        not isinstance(t.get("id"), str)
        or not isinstance(t.get("base_template_id", ""), (str, type(None)))
        for t in raw
    ):
        msg = "テンプレートIDは文字列で指定してください。"
        raise ValueError(msg)
    if len({t.get("id") for t in raw}) != len(raw):
        msg = "効果テンプレートのIDが重複しています。"
        raise ValueError(msg)
    return {t["id"]: t for t in raw}


def _valid_set_value(field, value):
    """未使用のセットも、既存の効果フィールドの型と範囲で検査する."""
    if isinstance(value, bool):
        return False
    for model in set(TEMPLATE_MODELS.values()):
        if field not in model.model_fields:
            continue
        try:
            parsed = TypeAdapter(
                model.model_fields[field].rebuild_annotation(),
            ).validate_python(value)
        except ValidationError:
            continue
        if field != "target_count" or parsed == "all" or 1 <= parsed <= 5:  # noqa: PLR2004
            return True
    return False


def _variant_set_entries(raw):
    """共有セットも空のカタログでもIDを検査する."""
    sets = TypeAdapter(list[VariantSet]).validate_python(raw or [])
    if len({s.id for s in sets}) != len(sets):
        msg = "変型セットのIDが重複しています。"
        raise ValueError(msg)
    for variant_set in sets:
        if any(
            not _valid_set_value(variant_set.field, v.value) for v in variant_set.values
        ):
            msg = f"{variant_set.name}の候補の型または範囲が不正です。"
            raise ValueError(msg)
    return {s.id: s for s in sets}


def _validate_attached_sets(result, sets, adapter):
    """選択前に全候補を検査し、使えない値を共有定義として保存しない."""
    base = result.model_dump()
    for variant_set in attached_sets(base, sets):
        if variant_set.field not in type(result).model_fields:
            msg = "この効果テンプレートでは変型セットの項目を使えません。"
            raise ValueError(msg)
        for candidate in variant_set.values:
            values = variant_values(
                base,
                {
                    "id": result.id,
                    "name": result.name,
                    "base_template_id": result.id,
                    variant_set.field: candidate.value,
                },
                [variant_set.field],
            )
            adapter.validate_python([values])


def _variant_details(template, base, path, allowed, sets):
    """個別差分と共有セットの参照を、混在させず同じ継承経路で解決する."""
    fields = _variant_override_fields(template, allowed)
    root_name, labels = path
    selections = template.get("variant_selections")
    if selections:
        if fields:
            msg = "セットの組み合わせに個別の上書きは指定できません。"
            raise ValueError(msg)
        changes, names = selected_values(base, template, sets)
        model = TEMPLATE_MODELS.get(str(changes.get("effect", base["effect"])))
        if model is None or set(changes) - set(model.model_fields):
            msg = "組み合わせた効果では、変型セットの項目を使えません。"
            raise ValueError(msg)
        values = variant_values(base, {**template, **changes}, list(changes))
        values["override_fields"] = []
        values["variant_set_ids"] = []
        labels = [*labels, *names]
    else:
        labels = [*labels, _variant_label(template, base["name"])]
        values = variant_values(base, template, fields)
    if len(labels) > MAX_VARIANT_DEPTH:
        msg = "変型は最大16段までです。"
        raise ValueError(msg)
    if template.get("variant_label") or selections:
        values["name"] = root_name + VARIANT_OPEN + ", ".join(labels) + VARIANT_CLOSE
    return values, (root_name, labels)


def resolve_template_variants(raw, variant_sets=None):
    """親を先に解決し、循環を拒否して最大16段までの差分を合成する."""
    adapter = TypeAdapter(list[SkillTemplate])
    entries = _template_entries(raw)
    if entries is None:
        return adapter.validate_python(raw)
    resolved = {}
    paths = {}
    visiting = set()
    allowed = set().union(
        *(set(model.model_fields) for model in TEMPLATE_MODELS.values()),
    )
    sets = _variant_set_entries(variant_sets)

    def resolve(identity):
        if identity in resolved:
            return resolved[identity]
        if identity in visiting:
            msg = "変型の参照が循環しています。"
            raise ValueError(msg)
        if identity not in entries:
            msg = "共通設定が見つかりません。"
            raise ValueError(msg)
        visiting.add(identity)
        if len(visiting) > MAX_VARIANT_DEPTH + 1:
            msg = "変型は最大16段までです。"
            raise ValueError(msg)
        template = entries[identity]
        base_id = template.get("base_template_id")
        if not base_id:
            result = adapter.validate_python([template])[0]
            paths[identity] = (result.name, [])
        else:
            base = resolve(base_id).model_dump()
            values, paths[identity] = _variant_details(
                template,
                base,
                paths[base_id],
                allowed,
                sets,
            )
            result = adapter.validate_python([values])[0]
        _validate_attached_sets(result, sets, adapter)
        resolved[identity] = result
        visiting.remove(identity)
        return result

    return [resolve(template["id"]) for template in raw]


def template_effect(template) -> dict:
    """旧ガンビット用の既定対象を補う。テンプレート本体には保存しない."""
    values = template.model_dump(
        exclude={
            "id",
            "name",
            "base_template_id",
            "override_fields",
            "variant_axis",
            "variant_label",
            "variant_set_ids",
            "variant_selections",
        },
    )
    values["target"] = (
        "self"
        if values.get("ailment") in {"regen", "refresh"}
        else "enemy"
        if values["effect"] in {"attack", "damage_mp", "debuff", "ailment"}
        else "self"
    )
    return values


def resolve_skill_templates(value):
    """参照した共通値を一意に解決し、個別の数値上書きを許さない."""
    if not isinstance(value, dict):
        return value
    templates, legacy_costs = read_effect_templates(
        value.get("skill_templates", default_skill_templates()),
        value.get("variant_sets", []),
    )
    by_id = {template.id: template for template in templates}
    if len(by_id) != len(templates):
        msg = "効果テンプレートのIDが重複しています。"
        raise ValueError(msg)
    skills = []
    if not isinstance(value.get("skills", []), list):
        msg = "スキル一覧は配列として指定してください。"
        raise ValueError(msg)  # noqa: TRY004 - Pydantic validator requires ValueError
    for original in value.get("skills", []):
        if not isinstance(original, (dict, BaseModel)):
            msg = "スキルはオブジェクトとして指定してください。"
            raise ValueError(msg)  # noqa: TRY004 - Pydantic validator requires ValueError
        skill = (
            original.model_dump() if isinstance(original, BaseModel) else dict(original)
        )
        references = skill.get("template_ids") or (
            [skill["template_id"]] if skill.get("template_id") else []
        )
        if references:
            if len(references) != len(set(references)):
                msg = "同じ効果テンプレートを重複して指定できません。"
                raise ValueError(msg)
            if missing := [
                reference for reference in references if reference not in by_id
            ]:
                msg = f"効果テンプレートが見つかりません: {missing[0]}"
                raise ValueError(msg)
            values = [template_effect(by_id[reference]) for reference in references]
            identity = {
                key: v for key, v in skill.items() if key in SKILL_IDENTITY_FIELDS
            }
            # Singular references retain their legacy template-owned MP semantics.
            if skill.get("template_ids"):
                identity["template_id"] = None
                skill = {
                    **values[0],
                    **identity,
                    "effects": values,
                }
            else:
                identity.setdefault("mp_cost", legacy_costs.get(references[0], 0))
                skill = {**values[0], **identity}
        skills.append(skill)
    return {**value, "skill_templates": templates, "skills": skills}
