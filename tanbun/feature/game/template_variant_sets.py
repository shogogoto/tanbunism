"""一項目の候補を複数の効果テンプレートで共有する変型セット."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .variant_sets import (
    VariantCandidate,
)

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


class VariantValue(VariantCandidate):
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
