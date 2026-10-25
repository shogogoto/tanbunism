"""一項目の候補を複数の効果テンプレートで共有する変型セット."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identity = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
VariantField = Literal[
    "effect",
    "damage_type",
    "target_count",
    "amount",
    "amount_mode",
    "multiplier",
    "duration_turns",
    "on_wrong",
    "stat",
    "affected_element",
    "stack_limit",
    "ailment",
]


class VariantValue(BaseModel):
    """安定した識別子と副名を持つ一つの候補."""

    model_config = ConfigDict(extra="forbid")
    id: Identity
    name: str = Field(min_length=1, max_length=60)
    value: str | int | Annotated[float, Field(allow_inf_nan=False)] | dict


class VariantSet(BaseModel):
    """変更する一項目と、値の候補."""

    model_config = ConfigDict(extra="forbid")
    id: Identity
    name: str = Field(min_length=1, max_length=80)
    field: VariantField
    values: list[VariantValue] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def unique_values(self):
        """副名を改名しても候補の参照を変えない."""
        if len({v.id for v in self.values}) != len(self.values):
            msg = "変型セットの候補IDが重複しています。"
            raise ValueError(msg)
        return self


class VariantSelection(BaseModel):
    """値を複製せず、使う候補を参照する."""

    model_config = ConfigDict(extra="forbid")
    set_id: Identity
    value_id: Identity


def attached_sets(template, sets):
    """同じ項目を変更するセットは、上書き順に依存させず拒否する."""
    ids = template.get("variant_set_ids", [])
    if len(ids) != len(set(ids)) or any(identity not in sets for identity in ids):
        msg = "変型セットの参照が重複しているか、見つかりません。"
        raise ValueError(msg)
    selected = [sets[identity] for identity in ids]
    if len({s.field for s in selected}) != len(selected):
        msg = "同じ項目を変更する変型セットは併用できません。"
        raise ValueError(msg)
    return selected


def selected_values(base, template, sets):
    """親に設定した順で候補を合成し、副名も同じ順に並べる."""
    attached = attached_sets(base, sets)
    selections = [
        VariantSelection.model_validate(s)
        for s in template.get("variant_selections", [])
    ]
    by_id = {s.set_id: s.value_id for s in selections}
    if len(by_id) != len(selections) or set(by_id) - {s.id for s in attached}:
        msg = (
            "組み合わせの変型セットが重複しているか、テンプレートに設定されていません。"
        )
        raise ValueError(msg)
    values, labels = {}, []
    for variant_set in attached:
        if variant_set.id not in by_id:
            continue
        candidate = next(
            (v for v in variant_set.values if v.id == by_id[variant_set.id]),
            None,
        )
        if candidate is None:
            msg = "変型セットの候補が見つかりません。"
            raise ValueError(msg)
        values[variant_set.field] = candidate.value
        labels.append(candidate.name)
    return values, labels
