"""一行動の戦闘状態更新。DB保存・回答履歴・通知は呼び出し側で扱う."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from .combat_models import CombatContext, TurnRequest
from .effect_chance import effect_activates
from .effects import add_effect, apply_player_periodic, sync_bubble
from .gambits import apply_action
from .inventory import apply_equipment_stats, equipment_wears_on_damage, wear_weapon
from .rewards import GoldSettings, roll_gold
from .skill_outcomes import skill_effects

if TYPE_CHECKING:
    from .state import BattleMarker, GameState

MOVES_PER_EVENT = 5
ENEMIES_TO_CLEAR = 3


def award_kill_gold(
    updated: GameState,
    body: TurnRequest,
    *,
    context: CombatContext | None = None,
) -> None:
    """撃破が確定した実戦のターンだけにゴールドを付ける."""
    gold = sum(
        roll_gold(
            context.gold if context else GoldSettings(),
            context.resource_power if context else 0,
            context.achievement if context else 0,
            "enemy",
        )
        for _ in set(body.defeated)
    )
    updated.save.economy.gold += gold
    if gold:
        updated.save.battleFeedback += f" · +{gold} G"


def apply_turn_result(
    previous: GameState,
    body: TurnRequest,
    results: dict[str, bool],
    damage: int,
    stats: dict,
    *,
    on_hit_skills: dict | None = None,
    base_stats: dict | None = None,
    context=None,
    equipment_hits: int | None = None,
) -> GameState:
    """相打ちの敗北優先・撤退先・歩数維持を一箇所で扱う."""
    marker, run = previous.save.battle, previous.save.run
    if not marker or not run:
        msg = "Missing battle"
        raise ValueError(msg)
    updated = previous.model_copy(deep=True)
    next_run = updated.save.run
    apply_on_hit_effects(updated.save.battle, on_hit_skills or {}, results)
    defensive = equipment_wears_on_damage(updated.save.economy)
    broken = (
        not defensive or updated.save.economy.equipment is not None
    ) and wear_weapon(
        updated.save.economy,
        sum(bool(results.get(e)) for e in marker.enemies)
        if equipment_hits is None
        else equipment_hits,
    )
    apply_combat_stats(
        next_run,
        updated.save.battle,
        stats,
        previous.save.economy,
        broken=broken,
        base_stats=apply_equipment_stats(base_stats, updated.save.economy)
        if base_stats is not None and updated.save.economy.equipment is not None
        else base_stats,
    )
    first_only = (
        marker.gambitAction
        and marker.gambitAction.skill.effect in {"attack", "debuff", "ailment"}
        and marker.gambitAction.skill.target == "enemy"
    )
    action_feedback = apply_action(
        updated.save,
        successful=results.get(marker.enemies[0], False)
        if first_only
        else any(results.values()),
        retreat=body.retreat,
        results=results,
        context=context,
    )
    periodic_hp, periodic_mp = apply_player_periodic(next_run, marker, damage)
    if defensive:
        broken = (
            wear_defensive_equipment(
                updated.save,
                damage + periodic_hp,
                stats,
                base_stats,
            )
            or broken
        )
    next_run.kills += 0 if marker.bossLevel else len(set(body.defeated))
    next_run.quizCursor += len(body.answers)
    survivors = [e for e in marker.enemies if e not in body.defeated]
    next_run.phase = (
        "defeated"
        if not next_run.hp
        else "rest"
        if body.retreat and next_run.moves >= MOVES_PER_EVENT
        else "path"
        if body.retreat
        else "battle"
        if survivors
        else "cleared"
        if next_run.kills >= ENEMIES_TO_CLEAR and not marker.bossLevel
        else "rest"
        if next_run.moves >= MOVES_PER_EVENT
        else "path"
    )
    if body.retreat or not next_run.hp:
        dungeon = updated.save.maps.get(run.resourceId)
        if dungeon:
            dungeon.current = marker.checkpoint if next_run.hp else "@entrance"
            if not next_run.hp and marker.discoveredPlace and not marker.bossLevel:
                failed = marker.discoveredPlace
                dungeon.places = [p for p in dungeon.places if p.id != failed]
                dungeon.edges = [
                    e for e in dungeon.edges if failed not in {e.from_, e.to}
                ]
                next_run.readIds = [p for p in next_run.readIds if p != failed]
    if next_run.phase == "cleared":
        updated.save.clears[run.resourceId] = (
            updated.save.clears.get(run.resourceId, 0) + 1
        )
    if next_run.phase == "battle":
        updated.save.battle.enemies = survivors
        updated.save.battle.effects = [
            e
            for e in updated.save.battle.effects
            if e.target_id == "player" or e.target_id in survivors
        ]
        updated.save.battle.turn += 1
        updated.save.battle.endActionDone = False
        # Starts only on explicit next-turn acknowledgement, not while reading results.
        next_run.answerDeadline = None
    else:
        updated.save.battle = None
        next_run.answerDeadline = None
    sync_bubble(
        next_run,
        updated.save.battle,
        (base_stats if broken and base_stats is not None else stats)["maxHp"],
        (base_stats if broken and base_stats is not None else stats)["maxMp"],
    )
    summary = "撤退" if body.retreat else "行動終了"
    updated.save.battleActionDetails = None
    updated.save.quizFeedback = (
        None
        if body.retreat
        else "時間切れ"
        if not body.answers
        else ("正解" if all(results.values()) else "不正解")
        if len(marker.enemies) == 1
        else f"{sum(results.values())}/{len(marker.enemies)}問正解"
    )
    updated.save.battleFeedback = summary
    updated.save.battleFeedback += status_feedback(
        broken=broken,
        hp=periodic_hp,
        mp=periodic_mp,
        defensive=defensive,
        slotted=updated.save.economy.equipment is not None,
    )
    if action_feedback:
        updated.save.battleFeedback += f" · {action_feedback}"
    record_boss_victory(updated, marker, body, survivors)
    return updated


def apply_combat_stats(run, marker, stats, economy, *, broken, base_stats=None) -> None:
    """装備破損時は全補正を外し、最大HPと残HPも同じ割合で戻す."""
    stats = stats.copy()
    if broken and base_stats is not None:
        old_base_hp = max(1, round(run.maxHp / run.hpScale))
        run.hp = max(1 if run.hp else 0, run.hp * base_stats["maxHp"] // old_base_hp)
        old_base_mp = round(run.maxMp / run.mpScale)
        run.mp = run.mp * base_stats["maxMp"] // old_base_mp if old_base_mp else 0
        stats = base_stats
    elif broken:
        stats |= {"attack": max(0, stats["attack"] - economy.weapon_attack)}
    for key, value in stats.items():
        if key not in {"maxHp", "maxMp"}:
            setattr(run, key, value)
    if marker and marker.schedule:
        marker.schedule.base_speeds["player"] = run.speed
    sync_bubble(run, marker, stats["maxHp"], stats["maxMp"])


def apply_on_hit_effects(marker: BattleMarker, skills: dict, results: dict) -> None:
    """誤答・未回答で敵の攻撃が命中したときだけ付与する."""
    for enemy, skill in skills.items():
        if enemy in marker.enemies and not results.get(enemy):
            for effect in skill_effects(skill):
                if effect.effect in {"debuff", "ailment"} and effect_activates(
                    effect,
                    marker,
                    "player",
                    enemy,
                ):
                    add_effect(marker, effect, "player")


def wear_defensive_equipment(save, damage, stats, base_stats=None) -> bool:
    """最後の一回も防御・HP補正を適用してから、装備の使用回数を減らす."""
    if damage <= 0 or not equipment_wears_on_damage(save.economy):
        return False
    snapshot = save.economy.model_copy(deep=True)
    broken = wear_weapon(save.economy, 1, defensive=True)
    if broken:
        apply_combat_stats(
            save.run,
            save.battle,
            stats,
            snapshot,
            broken=True,
            base_stats=apply_equipment_stats(base_stats, save.economy)
            if base_stats is not None and save.economy.equipment is not None
            else base_stats,
        )
    return broken


def status_feedback(
    *,
    broken: bool,
    hp: int,
    mp: int,
    defensive: bool = False,
    slotted: bool = False,
) -> str:
    """通常攻防以外の変化をまとめる."""
    return "".join(
        text
        for enabled, text in [
            (
                broken,
                " · 装備が壊れた"
                if slotted
                else " · 防具が壊れた"
                if defensive
                else " · 武器が壊れた",
            ),
            (hp, f" · 毒 HP -{hp}"),
            (mp, f" · 呪い MP -{mp}"),
        ]
        if enabled
    )


def record_boss_victory(
    updated: GameState,
    marker: BattleMarker,
    body: TurnRequest,
    survivors: list[str],
) -> None:
    """相打ちと撤退には開放を付けず、初回の節目だけを保存する."""
    if not marker.bossLevel or survivors or not updated.save.run.hp or body.retreat:
        return
    resource = UUID(str(updated.save.run.resourceId)).hex
    levels = updated.save.bossClears.setdefault(resource, [])
    if marker.bossLevel not in levels:
        levels.append(marker.bossLevel)
        levels.sort()
    updated.save.battleFeedback += " · ボス撃破・専門分野開放"
