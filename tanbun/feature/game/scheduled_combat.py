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
    apply_on_hit_effects,
    apply_turn_result,
    award_kill_gold,
    combat_context,
    evaluate_answers,
    persist,
)
from .damage import enemy_damage, player_damage
from .effect_chance import effect_activates
from .effects import (
    add_effect,
    apply_resource_effect,
    curse_damage,
    effect_amount,
    effect_unexpired,
    elemental_damage,
    modified_stat,
    periodic_recovery,
    poison_damage,
    sync_speed_effects,
)
from .enemy_gambits import choose_enemy_action
from .gambit_slots import gambit_capacity
from .gambits import BattleAction, choose_action
from .inventory import equipped_stats
from .skill_outcomes import (
    answer_effect,
    attack_stat,
    defense_stat,
    effect_targets,
    enemy_attack_value,
    normal_attack,
    skill_effects,
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
        if enemy.id not in targets or not effect_activates(skill, marker, enemy.id):
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
        outgoing[enemy.id] = min(
            marker.schedule.enemy_hp[enemy.id],
            elemental_damage(
                amount,
                skill,
                marker,
                "player",
                enemy.id,
                weaknesses=enemy.weaknesses,
                resistances=enemy.resistances,
                resistance_multiplier=context.balance.resistance_multiplier,
                weakness_multiplier=context.balance.weakness_multiplier,
                economy=state.save.economy,
                enabled_elements=context.enabled_elements,
            ),
        )
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
    return elemental_damage(
        max(0, int(base * skill.multiplier) + effect_amount(skill, attack)),
        skill,
        marker,
        enemy.id,
        "player",
        economy=state.save.economy,
        enabled_elements=context.enabled_elements,
    )


def enemy_action_skill(enemy, schedule, fallback, selected=None):
    """MP不足なら最下位の通常攻撃へ戻る。旧戦闘は初期MPから補う."""
    if selected:
        return selected.skill
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
        state.save.battle.enemyAction,
    )
    schedule.enemy_mp[enemy.id] = max(
        0,
        schedule.enemy_mp.get(enemy.id, enemy.mp) - skill.mp_cost,
    )


def apply_enemy_components(state, context, enemy, *, correct, retreat):
    """敵の複合行動も同じ正誤で回復・持続効果を処理する."""
    marker = state.save.battle
    schedule = marker.schedule
    if schedule.actor_id == "player" or retreat:
        return
    skill = enemy_action_skill(
        enemy,
        schedule,
        context.default_attack or normal_attack(),
        marker.enemyAction,
    )
    # Select before spending MP so the final paid action is not lost.
    spend_enemy_mp(state, context, enemy, retreat=retreat)
    for component in skill_effects(skill):
        effect = answer_effect(component, not correct)
        if not effect:
            continue
        schedule.enemy_max_hp.setdefault(enemy.id, enemy.hp)
        schedule.enemy_max_mp.setdefault(enemy.id, enemy.mp)
        targets = marker.enemyAction.target_ids if marker.enemyAction else None
        apply_resource_effect(
            state.save,
            effect,
            enemy.id,
            context=context,
            target_ids=targets,
        )
        if effect.effect in {"buff", "debuff", "ailment"}:
            for target in effect_targets(effect, marker, enemy.id, targets):
                if effect_activates(effect, marker, target, enemy.id):
                    add_effect(marker, effect, target)


def action_damage(state, context, *, correct, roll_player, roll_enemy):
    """正答は常にプレイヤーに有利。敵の行動ではスキルの成否を反転する。."""
    marker = state.save.battle
    schedule = marker.schedule
    target = schedule.quiz_enemy_id
    enemy = next(e for e in context.enemies if e.id == target)
    player_turn = schedule.actor_id == "player"
    action = marker.gambitAction if player_turn else None
    if action and action.target_ids:
        enemy = next((e for e in context.enemies if e.id in action.target_ids), enemy)
    fallback = context.default_attack or normal_attack()
    skill = (
        action.skill
        if action
        else fallback
        if player_turn
        else enemy_action_skill(enemy, schedule, fallback, marker.enemyAction)
    )
    outgoing, incoming, countered = {}, 0, False
    for component in skill_effects(skill):
        is_counter = (
            not (correct if player_turn else not correct)
            and getattr(component, "on_wrong", None)
            and component.on_wrong.type == "counter"
        )
        if is_counter and countered:
            continue
        dealt, received = component_damage(
            state,
            context,
            component,
            enemy,
            fallback,
            correct=correct,
            player_turn=player_turn,
            roll_player=roll_player,
            roll_enemy=roll_enemy,
        )
        for identity, amount in dealt.items():
            outgoing[identity] = min(
                schedule.enemy_hp[identity],
                outgoing.get(identity, 0) + amount,
            )
        incoming += received
        countered = countered or bool(is_counter)
    return outgoing, incoming


def component_damage(
    state,
    context,
    skill,
    enemy,
    fallback,
    *,
    correct,
    player_turn,
    roll_player,
    roll_enemy,
):
    """一つの効果のダメージを計算する。消費や状態付与は呼び出し側で一度だけ行う."""
    marker = state.save.battle
    target = marker.schedule.quiz_enemy_id
    successful = correct if player_turn else not correct
    effect = answer_effect(skill, successful)
    if effect and effect.effect == "attack":
        if effect.target == "self":
            amount = self_attack_damage(
                state,
                context,
                effect,
                schedule_actor=marker.schedule.actor_id,
                roll_enemy=roll_enemy,
            )
            if not effect_activates(
                effect,
                marker,
                marker.schedule.actor_id,
                marker.schedule.actor_id,
            ):
                amount = 0
            return ({}, amount) if player_turn else ({enemy.id: amount}, 0)
        if player_turn:
            return outgoing_attack(
                state,
                context,
                effect,
                skill_targets(
                    effect,
                    marker.enemies,
                    marker.gambitAction.target_ids if marker.gambitAction else None,
                ),
                roll_player=roll_player,
                roll_enemy=roll_enemy,
            ), 0
        return (
            enemy_targeted_damage(state, context, enemy, effect, roll_enemy)
            if marker.enemyAction and marker.enemyAction.target_ids is not None
            else (
                {},
                incoming_attack(state, context, enemy, effect, roll_enemy)
                if effect_activates(effect, marker, "player", enemy.id)
                else 0,
            )
        )
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


def enemy_targeted_damage(state, context, source, skill, roll_enemy):
    """敵の攻撃も確定済みの対象数・陣営へ適用する。."""
    marker = state.save.battle
    targets = effect_targets(skill, marker, source.id, marker.enemyAction.target_ids)
    outgoing, incoming = {}, 0
    enemies = {enemy.id: enemy for enemy in context.enemies}
    for target in targets:
        if not effect_activates(skill, marker, target, source.id):
            continue
        if target == "player":
            incoming += incoming_attack(state, context, source, skill, roll_enemy)
            continue
        enemy = enemies[target]
        attack = modified_stat(
            enemy_attack_value(source, skill),
            marker,
            source.id,
            attack_stat(skill),
        )
        defense = modified_stat(
            enemy.special_defense if skill.damage_type == "special" else enemy.defense,
            marker,
            target,
            defense_stat(skill),
        )
        amount = int(
            roll_enemy(attack, defense, context.balance) * skill.multiplier,
        ) + effect_amount(skill, attack)
        outgoing[target] = min(
            marker.schedule.enemy_hp[target],
            elemental_damage(
                amount,
                skill,
                marker,
                source.id,
                target,
                weaknesses=enemy.weaknesses,
                resistances=enemy.resistances,
                resistance_multiplier=context.balance.resistance_multiplier,
                weakness_multiplier=context.balance.weakness_multiplier,
                enabled_elements=context.enabled_elements,
                economy=state.save.economy,
            ),
        )
    return outgoing, incoming


def self_attack_damage(state, context, skill, *, schedule_actor, roll_enemy):
    """陣営はガンビットが決める。自分への攻撃も他陣営へすり替えない."""
    if schedule_actor == "player":
        stats = equipped_stats(
            state.save.allocation,
            context.balance,
            state.save.economy,
        )
        attack, defense = stats[attack_stat(skill)], stats[defense_stat(skill)]
    else:
        enemy = next(e for e in context.enemies if e.id == schedule_actor)
        attack = enemy_attack_value(enemy, skill)
        defense = (
            enemy.special_defense if skill.damage_type == "special" else enemy.defense
        )
    attack = modified_stat(
        attack,
        state.save.battle,
        schedule_actor,
        attack_stat(skill),
    )
    defense = modified_stat(
        defense,
        state.save.battle,
        schedule_actor,
        defense_stat(skill),
    )
    amount = max(
        0,
        int(roll_enemy(attack, defense, context.balance) * skill.multiplier)
        + effect_amount(skill, attack),
    )
    weaknesses = [] if schedule_actor == "player" else enemy.weaknesses
    resistances = [] if schedule_actor == "player" else enemy.resistances
    return elemental_damage(
        amount,
        skill,
        state.save.battle,
        schedule_actor,
        schedule_actor,
        weaknesses=weaknesses,
        resistances=resistances,
        resistance_multiplier=context.balance.resistance_multiplier,
        weakness_multiplier=context.balance.weakness_multiplier,
        economy=state.save.economy,
        enabled_elements=context.enabled_elements,
    )


def apply_enemy_periodic(state, context, marker) -> None:
    """敵にも同じ行動単位で毒・呪い・HP/MP継続回復を反映する."""
    schedule = state.save.battle.schedule
    hp, mp = schedule.enemy_hp, schedule.enemy_mp
    hp_max = {e.id: schedule.enemy_max_hp.get(e.id, e.hp) for e in context.enemies}
    mp_max = {e.id: schedule.enemy_max_mp.get(e.id, e.mp) for e in context.enemies}
    damage = poison_damage(marker, hp_max)
    hp_recovery = periodic_recovery(marker, hp_max, "hp")
    mp_recovery = periodic_recovery(marker, mp_max, "mp")
    mp_damage = curse_damage(marker, mp_max)
    for identity, maximum in hp_max.items():
        if hp.get(identity, 0) > 0:
            hp[identity] = min(
                maximum,
                max(1, hp[identity] - damage.get(identity, 0))
                + hp_recovery.get(identity, 0),
            )
            mp[identity] = min(
                mp_max[identity],
                max(
                    0,
                    mp.get(identity, mp_max[identity]) - mp_damage.get(identity, 0),
                )
                + mp_recovery.get(identity, 0),
            )


def scheduled_feedback(updated, context, body, *, correct, outgoing, incoming) -> str:
    """Show one verdict and meaningful events, preserving status/unlock notices."""
    # Drop the three legacy round totals while retaining subsequent events.
    extras = updated.save.battleFeedback.split(" · ", 3)[3:]
    if body.retreat and extras:
        extras = [extras[0].removeprefix("撤退").removeprefix(" · ")]
    messages = ["撤退" if body.retreat else "正解" if correct else "不正解"]
    names = {e.id: e.name for e in context.enemies}
    messages.extend(
        f"{names.get(identity, '敵')}に{amount}ダメージ"
        for identity, amount in outgoing.items()
        if amount > 0
    )
    if incoming > 0:
        messages.append(f"{incoming}ダメージを受けた")
    messages.extend(
        f"{names.get(identity, '敵')}を倒した" for identity in body.defeated
    )
    messages.extend(extra for extra in extras if extra)
    return " · ".join(messages)


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
    apply_enemy_components(
        working,
        context,
        enemy,
        correct=correct,
        retreat=body.retreat,
    )
    hp = working.save.battle.schedule.enemy_hp
    for identity, amount in outgoing.items():
        hp[identity] = max(0, hp[identity] - amount)
    apply_enemy_periodic(working, context, marker)
    defeated = [identity for identity in marker.enemies if hp[identity] <= 0]
    authoritative = body.model_copy(update={"defeated": defeated})
    targets = marker.gambitAction.target_ids if marker.gambitAction else None
    attacker = next((e for e in context.enemies if targets and e.id in targets), enemy)
    if incoming > 0 and attacker.on_hit_skill:
        apply_on_hit_effects(
            working.save.battle,
            {attacker.id: attacker.on_hit_skill},
            {attacker.id: False},
        )
    updated = apply_turn_result(
        working,
        authoritative,
        {target: correct} if target in body.answers else {},
        incoming,
        equipped_stats(state.save.allocation, context.balance, state.save.economy),
        base_stats=state.save.allocation.stats(context.balance),
        context=context,
        equipment_hits=int(any(amount > 0 for amount in outgoing.values())),
    )
    updated.save.battleFeedback = scheduled_feedback(
        updated,
        context,
        authoritative,
        correct=correct,
        outgoing=outgoing,
        incoming=incoming,
    )
    award_kill_gold(updated, authoritative, marker.region)
    if updated.save.battle:
        next_marker = updated.save.battle
        next_marker.schedule.actor_id = None
        sync_speed_effects(next_marker)
        next_marker.endActionDone = (
            schedule.actor_id != "player" or marker.gambitAction is not None
        )
    return {
        "state": updated,
        "results": {target: correct} if target in body.answers else {},
        "damage": incoming,
        "incoming_damage": {target: incoming} if incoming else {},
        "outgoing_damage": outgoing,
        "enemy_hp": updated.save.battle.schedule.enemy_hp
        if updated.save.battle
        else hp,
        "enemy_mp": updated.save.battle.schedule.enemy_mp
        if updated.save.battle
        else working.save.battle.schedule.enemy_mp,
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
