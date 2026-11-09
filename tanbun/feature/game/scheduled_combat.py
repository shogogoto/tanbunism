"""Server-authoritative, one-question-at-a-time CTB combat."""

import json
from secrets import choice
from time import time

from fastapi import HTTPException
from neomodel import adb

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.notification.usecase import dispatch_saved_notifications
from tanbun.feature.quiz.answering.repo import create_answer_in_transaction

from .action_queue import ActionSchedule, interval, predict, take_next
from .catalog import get_game_catalog
from .combat import (
    TurnRequest,
    combat_context,
    evaluate_answers,
    persist,
)
from .effects import (
    effect_unexpired,
    sync_speed_effects,
)
from .enemy_gambits import choose_enemy_action
from .gambit_slots import gambit_capacity
from .gambits import BattleAction, choose_action
from .inventory import equipped_stats
from .scheduled_resolution import (
    action_damage,
    apply_enemy_components,
    apply_enemy_periodic,
    component_damage,
    enemy_action_skill,
    enemy_targeted_damage,
    incoming_attack,
    outgoing_attack,
    resolve_scheduled,
    scheduled_action_details,
    scheduled_action_name,
    scheduled_action_outcome,
    scheduled_feedback,
    self_attack_damage,
    spend_enemy_mp,
)
from .skill_outcomes import (
    normal_attack,
)

__all__ = [
    "action_damage",
    "apply_enemy_components",
    "apply_enemy_periodic",
    "begin_scheduled_action",
    "component_damage",
    "enemy_action_skill",
    "enemy_targeted_damage",
    "incoming_attack",
    "initialize_schedule",
    "outgoing_attack",
    "prepare_live_gambit",
    "prepare_scheduled_gambit",
    "resolve_scheduled",
    "scheduled_action_details",
    "scheduled_action_name",
    "scheduled_action_outcome",
    "scheduled_feedback",
    "self_attack_damage",
    "settle_scheduled",
    "spend_enemy_mp",
]


def prepare_scheduled_gambit(state, catalog, slots=1) -> None:
    """Direct skills replace the player's normal action."""
    marker = state.save.battle
    if marker.schedule.actor_id == "player":
        selected = choose_action(state.save, catalog, slots=slots)
        marker.gambitAction = (
            selected if selected and not selected.specialty_id else None
        )
        if selected is None:
            fallback = normal_attack(catalog)
            marker.gambitAction = BattleAction(skill=fallback, name=fallback.name)


async def prepare_live_gambit(user_id, state) -> None:
    """Load available player actions for a newly sealed hand."""
    prepare_scheduled_gambit(
        state,
        await get_game_catalog(),
        (await gambit_capacity(user_id)).slots,
    )


def initialize_schedule(state, context) -> None:
    """Create server-owned HP and freeze this encounter's speed formula."""
    marker = state.save.battle
    enemies = {e.id: e for e in context.enemies if e.id in marker.enemies}
    if set(marker.enemies) != set(enemies):
        raise HTTPException(409, "戦闘の敵が不足しています。")
    speeds = {
        "player": state.save.run.speed,
        **{key: e.speed for key, e in enemies.items()},
    }
    marker.schedule = ActionSchedule(
        seed=marker.id,
        formula=context.balance.speed_formula,
        speeds=speeds,
        base_speeds=dict(speeds),
        waits={
            key: interval(speed, context.balance.speed_formula)
            for key, speed in speeds.items()
        },
        enemy_hp={key: e.hp for key, e in enemies.items()},
        enemy_mp={key: e.mp for key, e in enemies.items()},
        enemy_max_hp={key: e.hp for key, e in enemies.items()},
        enemy_max_mp={key: e.mp for key, e in enemies.items()},
    )
    begin_scheduled_action(state, context)


def begin_scheduled_action(state, context) -> None:
    """Seal actor, question and deadline once, independently of render/reload."""
    marker, run = state.save.battle, state.save.run
    schedule = marker.schedule
    enemies = {e.id: e for e in context.enemies if e.id in marker.enemies}
    schedule.base_speeds = {
        "player": run.speed,
        **{key: e.speed for key, e in enemies.items()},
    }
    schedule.stat_bases = {
        "player": equipped_stats(
            state.save.allocation,
            context.balance,
            state.save.economy,
        ),
        **{
            key: {
                "attack": e.unmodified_attack
                if e.unmodified_attack is not None
                else e.attack,
                "defense": e.defense,
                "specialAttack": e.special_attack
                if e.special_attack is not None
                else e.attack,
                "specialDefense": e.special_defense,
            }
            for key, e in enemies.items()
        },
    }
    sync_speed_effects(marker)
    actor = take_next(schedule)
    schedule.actor_id = actor
    target = marker.enemies[0] if actor == "player" else actor
    if target not in enemies:
        raise HTTPException(409, "戦闘クイズが更新されています。撤退してください。")
    schedule.quiz_enemy_id = target
    enemy = enemies[target]
    for identity, value in enemies.items():
        schedule.enemy_max_hp.setdefault(identity, value.hp)
        schedule.enemy_max_mp.setdefault(identity, value.mp)
    marker.quizIndices = {target: choice(enemy.quizIndexes or [enemy.quizIndex])}
    marker.gambitAction = None
    marker.enemyAction = None
    if actor != "player":
        marker.enemyAction = choose_enemy_action(state.save, enemy)
    marker.endActionDone = actor != "player"
    marker.effects = [e for e in marker.effects if effect_unexpired(e, marker.turn)]
    schedule.queue = predict(schedule)
    state.save.battleFeedback = None
    state.save.quizFeedback = None
    state.save.battleActionDetails = None
    run.answerSeconds = context.balance.base_seconds
    run.answerDeadline = int(time() * 1000) + run.answerSeconds * 1000


async def settle_scheduled(user_id, previous, body: TurnRequest) -> dict:
    """Save an action and its answer atomically under the battle revision."""
    marker, run = previous.save.battle, previous.save.run
    if set(body.answers) - {marker.schedule.quiz_enemy_id}:
        raise HTTPException(422, "現在の行動以外には回答できません。")
    if run.answerDeadline and int(time() * 1000) > run.answerDeadline:
        body = body.model_copy(update={"answers": {}})
    context = await combat_context(user_id, previous)
    results, quizzes = await evaluate_answers(
        user_id,
        previous,
        body,
        {e.id: e for e in context.enemies},
    )
    resolved = resolve_scheduled(previous, body, results, context)
    updated = resolved["state"]
    receipt = {key: value for key, value in resolved.items() if key != "state"}
    receipt["id"] = f"{marker.id}:{marker.turn}"
    notifications = []
    async with atomic():
        await persist(user_id, previous, updated)
        for identity, quiz_id in quizzes.items():
            _, saved = await create_answer_in_transaction(
                quiz_id,
                body.answers[identity],
                user_id,
                is_correct=results[identity],
            )
            notifications.extend(saved)
        await adb.cypher_query(
            "MATCH (u:User {uid:$uid}) SET u.game_turn_receipt=$receipt",
            {"uid": to_uuid(user_id).hex, "receipt": json.dumps(receipt)},
        )
    await dispatch_saved_notifications(user_id, notifications)
    return {**receipt, "state": updated.model_dump(by_alias=True)}
