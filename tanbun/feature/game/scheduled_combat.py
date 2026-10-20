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
    apply_on_hit_effects,
    apply_turn_result,
    award_kill_gold,
    combat_context,
    evaluate_answers,
    persist,
)
from .damage import enemy_damage, player_damage
from .effects import effect_amount, modified_stat, poison_damage
from .gambit_slots import gambit_capacity
from .gambits import BattleAction, choose_action
from .inventory import equipped_stats
from .skill_outcomes import (
    answer_effect,
    attack_stat,
    defense_stat,
    enemy_attack_value,
    normal_attack,
    skill_targets,
)


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
        waits={
            key: interval(speed, context.balance.speed_formula)
            for key, speed in speeds.items()
        },
        enemy_hp={key: e.hp for key, e in enemies.items()},
        enemy_mp={key: e.mp for key, e in enemies.items()},
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


def outgoing_attack(state, context, skill, targets, *, roll_player, roll_enemy):
    """プレイヤーから敵への、カテゴリ別能力を使った一回の攻撃。."""
    marker = state.save.battle
    stats = equipped_stats(state.save.allocation, context.balance, state.save.economy)
    attack = modified_stat(
        stats[attack_stat(skill)],
        marker,
        "player",
        attack_stat(skill),
    )
    outgoing = {}
    for enemy in context.enemies:
        if enemy.id not in targets:
            continue
        defense = modified_stat(
            enemy.special_defense if skill.damage_type == "special" else enemy.defense,
            marker,
            enemy.id,
            defense_stat(skill),
        )
        base = (
            roll_enemy(attack, defense, context.balance)
            if defense
            else roll_player(attack, context.balance)
        )
        amount = int(base * skill.multiplier) + effect_amount(skill, attack)
        outgoing[enemy.id] = min(marker.schedule.enemy_hp[enemy.id], max(0, amount))
    return outgoing


def incoming_attack(state, context, enemy, skill, roll_enemy):
    """敵からプレイヤーへの特殊攻撃・物理攻撃と通常反撃。."""
    marker = state.save.battle
    stats = equipped_stats(state.save.allocation, context.balance, state.save.economy)
    attack = modified_stat(
        enemy_attack_value(enemy, skill),
        marker,
        enemy.id,
        attack_stat(skill),
    )
    base = roll_enemy(
        attack,
        modified_stat(
            stats[defense_stat(skill)],
            marker,
            "player",
            defense_stat(skill),
        ),
        context.balance,
    )
    return max(0, int(base * skill.multiplier) + effect_amount(skill, attack))


def enemy_action_skill(enemy, schedule, fallback):
    """MP不足なら最下位の通常攻撃へ戻る。旧戦闘は初期MPから補う."""
    skill = enemy.attack_skill or fallback
    return (
        skill
        if schedule.enemy_mp.get(enemy.id, enemy.mp) >= skill.mp_cost
        else fallback
    )


def spend_enemy_mp(state, context, enemy, *, retreat):
    """行動の成否にかかわらず一回だけ消費し、撤退時は消費しない."""
    schedule = state.save.battle.schedule
    if schedule.actor_id == "player" or retreat:
        return
    skill = enemy_action_skill(
        enemy,
        schedule,
        context.default_attack or normal_attack(),
    )
    schedule.enemy_mp[enemy.id] = max(
        0,
        schedule.enemy_mp.get(enemy.id, enemy.mp) - skill.mp_cost,
    )


def action_damage(state, context, *, correct, roll_player, roll_enemy):
    """正答は常にプレイヤーに有利。敵の行動ではスキルの成否を反転する。."""
    marker = state.save.battle
    schedule = marker.schedule
    target = schedule.quiz_enemy_id
    enemy = next(e for e in context.enemies if e.id == target)
    player_turn = schedule.actor_id == "player"
    action = marker.gambitAction if player_turn else None
    fallback = context.default_attack or normal_attack()
    skill = (
        action.skill
        if action
        else fallback
        if player_turn
        else enemy_action_skill(enemy, schedule, fallback)
    )
    successful = correct if player_turn else not correct
    effect = answer_effect(skill, successful)
    if effect and effect.effect == "attack":
        if player_turn:
            return outgoing_attack(
                state,
                context,
                effect,
                skill_targets(effect, marker.enemies),
                roll_player=roll_player,
                roll_enemy=roll_enemy,
            ), 0
        return {}, incoming_attack(state, context, enemy, effect, roll_enemy)
    if not successful and skill.on_wrong.type == "counter":
        if player_turn:
            return {}, incoming_attack(
                state,
                context,
                enemy,
                fallback,
                roll_enemy,
            )
        return outgoing_attack(
            state,
            context,
            fallback,
            [target],
            roll_player=roll_player,
            roll_enemy=roll_enemy,
        ), 0
    return {}, 0


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
    if body.retreat:
        outgoing = {}
    working = state.model_copy(deep=True)
    spend_enemy_mp(working, context, enemy, retreat=body.retreat)
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
    if incoming > 0 and enemy.on_hit_skill:
        apply_on_hit_effects(
            working.save.battle,
            {target: enemy.on_hit_skill},
            {target: False},
        )
    updated = apply_turn_result(
        working,
        authoritative,
        {target: correct} if target in body.answers else {},
        incoming,
        equipped_stats(state.save.allocation, context.balance, state.save.economy),
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
        "enemy_mp": working.save.battle.schedule.enemy_mp,
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
