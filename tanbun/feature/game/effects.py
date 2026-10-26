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
    resistances=(),
    resistance_multiplier=0.5,
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
    affinity = (
        weakness_multiplier
        if element in weaknesses
        else resistance_multiplier
        if element in resistances
        else 1
    )
    return max(
        0,
        min(
            100000,
            floor(
                amount
                * affinity
                * max(0, 100 + outgoing)
                * max(0, 100 + incoming)
                / 10000,
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
            or not effect_active(effect, marker.turn)
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
    expires_turn: int | None = Field(default=None, ge=1)


def effect_unexpired(effect: BattleEffect, turn: int) -> bool:
    """無限の持続数は戦闘中に期限切れにならない."""
    return effect.expires_turn is None or turn < effect.expires_turn


def effect_active(effect: BattleEffect, turn: int) -> bool:
    """開始前の効果と期限切れを、すべての集計で共通に除外する."""
    return effect.starts_turn <= turn and effect_unexpired(effect, turn)


def effect_expiry(turn: int, duration: int | str) -> int | None:
    """無限を巨大な仮のターン数には置き換えない."""
    return None if duration == "infinite" else turn + int(duration)


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
    target_ids: list[str] | None = None,
) -> None:
    """HP・MPの増減を、ガンビットが選んだ陣営へ上限内で適用する."""
    if skill.effect not in {"heal_hp", "heal_mp", "damage_mp"}:
        return
    from .effect_chance import effect_activates  # noqa: PLC0415
    from .skill_outcomes import skill_targets  # noqa: PLC0415 - avoid catalog cycle

    marker, run = save.battle, save.run
    targets = (
        [actor]
        if skill.target == "self"
        else skill_targets(skill, marker.enemies, target_ids)
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
    resistances = (
        {enemy.id: enemy.resistances for enemy in context.enemies}
        if context
        else affinities.get("resistances", {})
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
                resistances=resistances.get(target, []),
                resistance_multiplier=context.balance.resistance_multiplier
                if context
                else affinities.get("resistance_multiplier", 0.5),
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
        if not effect_activates(skill, marker, target, actor):
            continue
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
        and effect_active(effect, marker.turn)
    )
    return max(0, min(100000, base + change))


def sync_speed_effects(marker) -> None:
    """速さの状態効果を進行中のゲージと行動予測へ反映。再同期で累積しない."""
    if not marker or not marker.schedule:
        return
    from .action_queue import predict, refresh_speeds  # noqa: PLC0415

    schedule = marker.schedule
    if not schedule.base_speeds:
        schedule.base_speeds = dict(schedule.speeds)
    refresh_speeds(
        schedule,
        {
            actor: max(1, modified_stat(base, marker, actor, "speed"))
            for actor, base in schedule.base_speeds.items()
            if actor == "player" or actor in marker.enemies
        },
    )
    schedule.queue = predict(schedule)


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
            and effect_active(e, marker.turn)
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
            and effect_active(e, marker.turn)
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
                and effect_active(effect, marker.turn)
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
    marker.effects = [e for e in marker.effects if effect_unexpired(e, marker.turn)]
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
            expires_turn=effect_expiry(marker.turn + 1, skill.duration_turns),
        ),
    )


def bubble_factor(
    marker: "BattleMarker | None",
    stat: str = "maxHp",
    target: str = "player",
) -> float:
    """HP/MPへの割合buff・debuffを共通に集計する."""
    if not marker:
        return 1.0
    return max(
        0,
        1
        + sum(
            e.skill.amount / 100 * (1 if e.skill.effect == "buff" else -1)
            for e in marker.effects
            if e.target_id == target
            and e.skill.effect in {"buff", "debuff"}
            and e.skill.stat == stat
            and effect_active(e, marker.turn)
        ),
    )


def scaled_resource(current: int, base: int, old_scale: float, factor: float):
    """最大値と残量を同じ比率で伸縮。0MP・戦闘不能も維持する."""
    if base == 0:
        return 0, 0, 1.0
    maximum = max(1, min(100000, floor(round(base * factor, 9))))
    scale = maximum / base
    remaining = min(
        maximum,
        max(1 if current else 0, floor(round(current * scale / old_scale, 9))),
    )
    return remaining, maximum, scale


def sync_enemy_resources(marker) -> None:
    """敵も同じ最大値・残量のルールを使い、解除時に元へ戻す."""
    if not marker or not marker.schedule:
        return
    schedule = marker.schedule
    for resource, stat in (("hp", "maxHp"), ("mp", "maxMp")):
        values = getattr(schedule, f"enemy_{resource}")
        maxima = getattr(schedule, f"enemy_max_{resource}")
        scales = getattr(schedule, f"enemy_{resource}_scale")
        for target, old_maximum in maxima.items():
            old = scales.get(target, 1)
            current, maximum, scale = scaled_resource(
                values.get(target, 0),
                round(old_maximum / old),
                old,
                bubble_factor(marker, stat, target),
            )
            values[target], maxima[target], scales[target] = current, maximum, scale


def sync_bubble(
    run,
    marker: "BattleMarker | None",
    base_max_hp: int | None = None,
    base_max_mp: int | None = None,
) -> None:
    """付与・解除・戦闘終了でHP/MPの最大値と残量を一緒に伸縮する."""
    for resource, stat, supplied in (
        ("hp", "maxHp", base_max_hp),
        ("mp", "maxMp", base_max_mp),
    ):
        scale_key = f"{resource}Scale"
        old = getattr(run, scale_key)
        base = supplied if supplied is not None else round(getattr(run, stat) / old)
        current, maximum, scale = scaled_resource(
            getattr(run, resource),
            base,
            old,
            bubble_factor(marker, stat),
        )
        setattr(run, resource, current)
        setattr(run, stat, maximum)
        setattr(run, scale_key, scale)
    sync_enemy_resources(marker)
