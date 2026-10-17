"""戦闘内に閉じる能力変化。付与した次のターンから指定ターン数だけ有効."""

from math import floor
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .catalog import SkillDefinition, StoredSkill

MAX_BATTLE_EFFECTS = 200

if TYPE_CHECKING:
    from .state import BattleMarker


class BattleEffect(BaseModel):
    """付与時の定義と有効期間を固定した効果一つ分."""

    skill: StoredSkill
    target_id: str = Field(max_length=200)
    starts_turn: int = Field(ge=0)
    expires_turn: int = Field(ge=1)


def effect_amount(skill: SkillDefinition, base: int) -> int:
    """割合は元の値を基準とし、重複で指数的に増えないようにする."""
    return (
        floor(base * skill.amount / 100)
        if getattr(skill, "amount_mode", "fixed") == "percent"
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
        and effect.skill.effect in {"buff", "debuff"}
        and effect.skill.stat == stat
        and effect.starts_turn <= marker.turn < effect.expires_turn
    )
    return max(0, min(100000, base + change))


def poison_damage(marker: "BattleMarker", maximum_hp: dict[str, int]) -> dict[str, int]:
    """ターン末の毒ダメージ。残HPへの適用時は1を残し、撃破には正解が必要."""
    result = {}
    for target, hp in maximum_hp.items():
        damage = sum(
            effect_amount(e.skill, hp)
            for e in marker.effects
            if e.target_id == target
            and e.skill.effect == "ailment"
            and e.skill.ailment == "poison"
            and e.starts_turn <= marker.turn < e.expires_turn
        )
        if damage:
            result[target] = min(100000, damage)
    return result


def curse_damage(marker: "BattleMarker", maximum_mp: dict[str, int]) -> dict[str, int]:
    """呪いは有効ターンごとにMPだけを減らし、毒とは混ぜない."""
    result = {}
    for target, mp in maximum_mp.items():
        amount = sum(
            effect_amount(e.skill, mp)
            for e in marker.effects
            if e.target_id == target
            and e.skill.effect == "ailment"
            and e.skill.ailment == "curse"
            and e.starts_turn <= marker.turn < e.expires_turn
        )
        if amount:
            result[target] = min(100000, amount)
    return result


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


def bubble_factor(marker: "BattleMarker | None") -> float:
    """最大HPへの割合buffを合算し、現在HPにも同じ倍率を使う."""
    if not marker:
        return 1.0
    return 1 + sum(
        e.skill.amount / 100
        for e in marker.effects
        if e.target_id == "player"
        and e.skill.effect == "buff"
        and e.skill.stat == "maxHp"
        and e.starts_turn <= marker.turn < e.expires_turn
    )


def sync_bubble(
    run,
    marker: "BattleMarker | None",
    base_max_hp: int | None = None,
) -> None:
    """付与・解除・戦闘終了で最大HPと残HPを一緒に伸縮する."""
    old = run.hpScale
    base = base_max_hp if base_max_hp is not None else max(1, round(run.maxHp / old))
    maximum = max(1, min(100000, floor(base * bubble_factor(marker))))
    factor = maximum / base
    run.hp = min(maximum, max(1 if run.hp else 0, floor(run.hp * factor / old)))
    run.maxHp = maximum
    run.hpScale = factor
