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

from .action_queue import ActionSchedule, interval, predict, refresh_speeds, take_next
from .catalog import get_game_catalog
from .combat import (
    TurnRequest,
    apply_turn_result,
    award_kill_gold,
    combat_context,
    evaluate_answers,
    persist,
)
from .damage import enemy_damage, player_damage
from .effects import effect_amount, modified_stat, poison_damage
from .gambit_slots import gambit_capacity
from .gambits import choose_action
from .inventory import equipped_stats


def prepare_scheduled_gambit(state, catalog, slots=1) -> None:
    """Direct skills replace the player's normal action."""
    marker = state.save.battle
    if marker.schedule.actor_id == "player":
        selected = choose_action(state.save, catalog, slots=slots)
        marker.gambitAction = (
            selected if selected and not selected.specialty_id else None
        )


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
        waits={
            key: interval(speed, context.balance.speed_formula)
            for key, speed in speeds.items()
        },
        enemy_hp={key: e.hp for key, e in enemies.items()},
    )
    begin_scheduled_action(state, context)


def begin_scheduled_action(state, context) -> None:
    """Seal actor, question and deadline once, independently of render/reload."""
    marker, run = state.save.battle, state.save.run
    schedule = marker.schedule
    enemies = {e.id: e for e in context.enemies if e.id in marker.enemies}
    refresh_speeds(
        schedule,
        {
            "player": run.speed,
            **{key: e.speed for key, e in enemies.items()},
        },
    )
    actor = take_next(schedule)
    schedule.actor_id = actor
    target = marker.enemies[0] if actor == "player" else actor
    if target not in enemies:
        raise HTTPException(409, "戦闘クイズが更新されています。撤退してください。")
    schedule.quiz_enemy_id = target
    enemy = enemies[target]
    marker.quizIndices = {target: choice(enemy.quizIndexes or [enemy.quizIndex])}
    marker.gambitAction = None
    marker.endActionDone = actor != "player"
    marker.effects = [e for e in marker.effects if e.expires_turn > marker.turn]
    schedule.queue = predict(schedule)
    state.save.battleFeedback = None
    run.answerSeconds = context.balance.base_seconds
    run.answerDeadline = int(time() * 1000) + run.answerSeconds * 1000


def action_damage(state, context, *, correct, roll_player, roll_enemy):
    """Calculate normal risky attacks or the sealed gambit's effect."""
    marker = state.save.battle
    schedule = marker.schedule
    target = schedule.quiz_enemy_id
    enemies = {e.id: e for e in context.enemies}
    enemy = enemies[target]
    stats = equipped_stats(state.save.allocation, context.balance, state.save.economy)
    action = marker.gambitAction
    outgoing = {}
    incoming = 0
    if correct:
        attack = modified_stat(stats["attack"], marker, "player", "attack")
        if not action or action.skill.effect == "attack":
            targets = (
                list(marker.enemies)
                if action and action.skill.target == "all_enemies"
                else [target]
            )
            for identity in targets:
                amount = roll_player(attack, context.balance)
                if action:
                    amount = int(amount * action.skill.multiplier) + effect_amount(
                        action.skill,
                        attack,
                    )
                outgoing[identity] = min(schedule.enemy_hp[identity], max(0, amount))
    elif not action or schedule.actor_id != "player":
        incoming = roll_enemy(
            modified_stat(enemy.attack, marker, target, "attack"),
            modified_stat(stats["defense"], marker, "player", "defense"),
            context.balance,
        )
    return outgoing, incoming


def resolve_scheduled(
    state,
    body,
    results,
    context,
    *,
    roll_player=player_damage,
    roll_enemy=enemy_damage,
):
    """Derive HP and kills on the backend, ignoring client damage claims."""
    marker = state.save.battle
    schedule = marker.schedule
    if state.save.battleFeedback or not schedule.actor_id:
        raise HTTPException(409, "この行動は精算済みです。")
    target = schedule.quiz_enemy_id
    if set(body.answers) - {target}:
        raise HTTPException(422, "現在の行動以外には回答できません。")
    enemy = next((e for e in context.enemies if e.id == target), None)
    if not enemy:
        raise HTTPException(409, "戦闘の敵が更新されました。")
    correct = bool(results.get(target)) and not body.retreat
    outgoing, incoming = action_damage(
        state,
        context,
        correct=correct,
        roll_player=roll_player,
        roll_enemy=roll_enemy,
    )
    working = state.model_copy(deep=True)
    hp = working.save.battle.schedule.enemy_hp
    for identity, amount in outgoing.items():
        hp[identity] = max(0, hp[identity] - amount)
    for identity, amount in poison_damage(
        marker,
        {e.id: e.hp for e in context.enemies},
    ).items():
        if hp.get(identity, 0) > 0:
            hp[identity] = max(1, hp[identity] - amount)
    defeated = [identity for identity in marker.enemies if hp[identity] <= 0]
    authoritative = body.model_copy(update={"defeated": defeated})
    updated = apply_turn_result(
        working,
        authoritative,
        {target: correct} if target in body.answers else {},
        incoming,
        equipped_stats(state.save.allocation, context.balance, state.save.economy),
        on_hit_skills={target: enemy.on_hit_skill} if enemy.on_hit_skill else {},
    )
    updated.save.battleFeedback = updated.save.battleFeedback.replace(
        f"/{len(marker.enemies)} ·",
        "/1 ·",
        1,
    )
    award_kill_gold(updated, authoritative, marker.region)
    if updated.save.battle:
        next_marker = updated.save.battle
        next_marker.schedule.actor_id = None
        refresh_speeds(
            next_marker.schedule,
            {
                key: value
                for key, value in schedule.speeds.items()
                if key == "player" or key in next_marker.enemies
            },
        )
        next_marker.schedule.queue = predict(next_marker.schedule)
        next_marker.endActionDone = (
            schedule.actor_id != "player" or marker.gambitAction is not None
        )
    return {
        "state": updated,
        "results": {target: correct} if target in body.answers else {},
        "damage": incoming,
        "incoming_damage": {target: incoming} if incoming else {},
        "outgoing_damage": outgoing,
        "enemy_hp": hp,
        "actor_id": schedule.actor_id,
        "quiz_enemy_id": target,
        "gold_earned": updated.save.economy.gold - state.save.economy.gold,
    }


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
