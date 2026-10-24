"""戦闘内に閉じる能力変化。付与した次のターンから指定ターン数だけ有効."""

from math import floor
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .catalog import SkillDefinition, StoredSkill

MAX_BATTLE_EFFECTS = 200

if TYPE_CHECKING:
    from .state import BattleMarker


def elemental_damage(
    amount,
    skill,
    marker,
    source,
    target,
    *,
    weaknesses=(),
    weakness_multiplier=1.5,
    economy=None,
    enabled_elements=None,
) -> int:
    """弱点と属性補正を一箇所で合成。各方向の補正は基準100%へ加算する."""
    element = getattr(skill, "element", "none")
    if element == "none" or (
        enabled_elements is not None and element not in enabled_elements
    ):
        return max(0, min(100000, amount))
    outgoing, incoming = element_adjustments(marker, element, source, target)
    if economy and economy.weapon and economy.inventory.get(economy.weapon, 0) > 0:
        for buff in economy.equipment_buffs or []:
            if buff.affected_element != element:
                continue
            if source == "player" and buff.stat == "damageDealt":
                outgoing += buff.amount
            if target == "player" and buff.stat == "damageTaken":
                incoming -= buff.amount
    weak = weakness_multiplier if element in weaknesses else 1
    return max(
        0,
        min(
            100000,
            floor(
                amount * weak * max(0, 100 + outgoing) * max(0, 100 + incoming) / 10000,
            ),
        ),
    )


def element_adjustments(marker, element, source, target):
    """その行動で有効な状態だけを方向別に集計する."""
    outgoing, incoming = 0, 0
    for effect in marker.effects if marker else []:
        value = effect.skill
        if (
            value.effect not in {"buff", "debuff"}
            or value.affected_element != element
            or not effect.starts_turn <= marker.turn < effect.expires_turn
        ):
            continue
        if value.stat == "damageDealt" and effect.target_id == source:
            outgoing += value.amount * (1 if value.effect == "buff" else -1)
        if value.stat == "damageTaken" and effect.target_id == target:
            incoming += value.amount * (-1 if value.effect == "buff" else 1)
    return outgoing, incoming


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


def apply_resource_effect(
    save,
    skill: SkillDefinition,
    actor: str = "player",
    *,
    context=None,
    affinities=None,
) -> None:
    """HP・MPの増減を、ガンビットが選んだ陣営へ上限内で適用する."""
    if skill.effect not in {"heal_hp", "heal_mp", "damage_mp"}:
        return
    from .skill_outcomes import skill_targets  # noqa: PLC0415 - avoid catalog cycle

    marker, run = save.battle, save.run
    targets = (
        [actor]
        if skill.target == "self"
        else skill_targets(skill, marker.enemies)
        if actor == "player"
        else ["player"]
    )
    hp = skill.effect == "heal_hp"
    sign = -1 if skill.effect == "damage_mp" else 1
    affinities = affinities or {}
    weaknesses = (
        {enemy.id: enemy.weaknesses for enemy in context.enemies}
        if context
        else affinities.get("weaknesses", {})
    )

    def amount_for(target, maximum):
        amount = effect_amount(skill, maximum)
        return (
            elemental_damage(
                amount,
                skill,
                marker,
                actor,
                target,
                weaknesses=weaknesses.get(target, []),
                weakness_multiplier=context.balance.weakness_multiplier
                if context
                else affinities.get("weakness_multiplier", 1.5),
                enabled_elements=context.enabled_elements
                if context
                else affinities.get("enabled_elements"),
                economy=save.economy,
            )
            if skill.effect == "damage_mp"
            else amount
        )

    for target in targets:
        if target == "player":
            key, maximum = ("hp", run.maxHp) if hp else ("mp", run.maxMp)
            setattr(
                run,
                key,
                max(
                    0,
                    min(
                        maximum,
                        getattr(run, key) + sign * amount_for(target, maximum),
                    ),
                ),
            )
        elif marker.schedule:
            schedule = marker.schedule
            if schedule.enemy_hp.get(target, 0) <= 0:
                continue
            values = schedule.enemy_hp if hp else schedule.enemy_mp
            limits = schedule.enemy_max_hp if hp else schedule.enemy_max_mp
            maximum = limits.get(target, values.get(target, 0))
            values[target] = max(
                0,
                min(
                    maximum,
                    values.get(target, 0) + sign * amount_for(target, maximum),
                ),
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


def periodic_recovery(
    marker: "BattleMarker",
    maxima: dict[str, int],
    resource: str,
) -> dict[str, int]:
    """リジェネ・MP継続回復は、毒と同じ行動数・重複ルールで集計する."""
    kind = "regen" if resource == "hp" else "refresh"
    return {
        target: min(
            100000,
            sum(
                effect_amount(effect.skill, maximum)
                for effect in marker.effects
                if effect.target_id == target
                and effect.skill.effect == "ailment"
                and effect.skill.ailment == kind
                and effect.starts_turn <= marker.turn < effect.expires_turn
            ),
        )
        for target, maximum in maxima.items()
    }


def apply_player_periodic(run, marker, damage: int) -> tuple[int, int]:
    """行動完了時のHP・MP増減。継続回復では戦闘不能から復活しない."""
    hp_damage = poison_damage(marker, {"player": run.maxHp}).get("player", 0)
    mp_damage = curse_damage(marker, {"player": run.maxMp}).get("player", 0)
    run.hp = max(
        0,
        min(run.hp, run.maxHp) - damage - hp_damage,
    )
    run.mp = max(
        0,
        min(run.mp, run.maxMp) - mp_damage,
    )
    if run.hp:
        run.hp = min(
            run.maxHp,
            run.hp
            + periodic_recovery(marker, {"player": run.maxHp}, "hp").get("player", 0),
        )
        run.mp = min(
            run.maxMp,
            run.mp
            + periodic_recovery(marker, {"player": run.maxMp}, "mp").get("player", 0),
        )
    return hp_damage, mp_damage


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
