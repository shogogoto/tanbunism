"""戦闘内に閉じる能力変化。付与した次のターンから指定ターン数だけ有効."""

from math import floor
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .catalog import SkillDefinition

MAX_BATTLE_EFFECTS = 200

if TYPE_CHECKING:
    from .state import BattleMarker


class BattleEffect(BaseModel):
    """付与時の定義と有効期間を固定した効果一つ分."""

    skill: SkillDefinition
    target_id: str = Field(max_length=200)
    starts_turn: int = Field(ge=0)
    expires_turn: int = Field(ge=1)


def effect_amount(skill: SkillDefinition, base: int) -> int:
    """割合は元の値を基準とし、重複で指数的に増えないようにする."""
    return (
        floor(base * skill.amount / 100)
        if skill.amount_mode == "percent"
        else skill.amount
    )


def modified_stat(
    base: int,
    marker: "BattleMarker | None",
    target: str,
    stat: str,
) -> int:
    """有効な効果だけ加減し、基礎値そのものは変更しない."""
    if not marker:
        return base
    change = sum(
        effect_amount(effect.skill, base) * (1 if effect.skill.effect == "buff" else -1)
        for effect in marker.effects
        if effect.target_id == target
        and effect.skill.stat == stat
        and effect.starts_turn <= marker.turn < effect.expires_turn
    )
    return max(0, min(100000, base + change))


def add_effect(marker: "BattleMarker", skill: SkillDefinition, target: str) -> None:
    """重複不可なら期限を更新。重複可なら古い分から上限まで置き換える."""
    marker.effects = [e for e in marker.effects if e.expires_turn > marker.turn]
    matching = [
        e for e in marker.effects if e.skill.id == skill.id and e.target_id == target
    ]
    for old in matching[: max(0, len(matching) - skill.stack_limit + 1)]:
        marker.effects.remove(old)
    if len(marker.effects) >= MAX_BATTLE_EFFECTS:
        marker.effects.pop(0)
    marker.effects.append(
        BattleEffect(
            skill=skill.model_copy(deep=True),
            target_id=target,
            starts_turn=marker.turn + 1,
            expires_turn=marker.turn + 1 + skill.duration_turns,
        ),
    )
