"""通常戦闘後に一度だけ実行するガンビットと、リソースごとの独立クイズ."""

from math import floor
from secrets import choice
from time import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.notification.usecase import dispatch_saved_notifications
from tanbun.feature.quiz.answering.repo import create_answer_in_transaction
from tanbun.feature.quiz.chain.usecase import expand_quiz_chain

from .action_details import BattleActionDetails, describe_action
from .catalog import get_game_catalog, parse_skill
from .combat import (
    ENEMIES_TO_CLEAR,
    MOVES_PER_EVENT,
    combat_context,
    persist,
    wear_defensive_equipment,
)
from .economy import GOLD_PER_LEVEL
from .effect_chance import effect_activates
from .effects import (
    add_effect,
    apply_resource_effect,
    effect_amount,
    effect_expiry,
    elemental_damage,
    sync_bubble,
    sync_speed_effects,
)
from .gambit_slots import gambit_capacity
from .gambits import choose_action
from .inventory import (
    consume_use,
    equipment_wears_on_damage,
    equipped_stats,
    wear_weapon,
)
from .skill_outcomes import (
    answer_effect,
    attack_stat,
    has_counter,
    normal_attack,
    skill_effects,
    skill_targets,
)
from .skills import specialty_pools
from .specialties import specialty_capacity, strengthened
from .state import GameState, read_state


class EndActionRequest(BaseModel):
    """ターンの更新番号と、生存している敵のブラウザ内HP."""

    battle_id: str
    turn: int = Field(ge=0)
    enemy_hp: dict[str, int] = Field(default_factory=dict, max_length=20)


class EndActionAnswer(BaseModel):
    """回答番号による冪等性。次の問題へ進んだ後の再送を再採点しない."""

    action_id: str
    index: int = Field(ge=0)
    selected: list[str] | None = Field(default=None, max_length=100)


async def action_view(user_id, state: GameState, outgoing=None) -> dict:
    """保存済みの問題を返す。再読み込みで時間や問題を再抽選しない."""
    action = state.save.battle.endAction if state.save.battle else None
    quiz = None
    if action and action["index"] < len(action["quiz_ids"]):
        chain = await expand_quiz_chain(
            user_id,
            to_uuid(action["quiz_ids"][action["index"]]),
        )
        quiz = chain.quizzes[0].readable
    return {
        "state": state,
        "action": action,
        "quiz": quiz,
        "outgoing_damage": outgoing or {},
        "animation": (state.save.endActionReceipt or {}).get("animation", "none")
        if not action
        else "none",
    }


async def start_end_action(user_id, body: EndActionRequest) -> dict:
    """精算済みの通常ターンに対して一度だけ選択・消費する."""
    previous = await read_state(user_id)
    marker, run = previous.save.battle, previous.save.run
    receipt = previous.save.endActionReceipt or {}
    if (receipt.get("battle_id"), receipt.get("turn")) == (body.battle_id, body.turn):
        return await action_view(user_id, previous, receipt.get("outgoing_damage"))
    if (
        not marker
        or not run
        or (marker.id, marker.turn) != (body.battle_id, body.turn)
        or not previous.save.battleFeedback
        or not run.hp
    ):
        raise HTTPException(409, "通常ターンの結果処理後に実行してください。")
    if marker.endAction or marker.endActionDone:
        return await action_view(user_id, previous)
    updated = previous.model_copy(deep=True)
    updated.save.endActionReceipt = None
    marker = updated.save.battle
    selected, skill, quizzes = await select_end_candidate(user_id, updated.save)
    marker.endActionDone = True
    if not selected:
        async with atomic():
            await persist(user_id, previous, updated)
        return await action_view(user_id, updated)
    context = await combat_context(user_id, updated)
    begin_action(
        updated.save,
        selected,
        skill,
        quizzes,
        marker.schedule.enemy_hp if marker.schedule else body.enemy_hp,
        counter_damage=prepare_counter_damage(
            updated,
            context,
            selected.target_ids,
        )
        if quizzes and has_counter(skill)
        else 0,
        context=context,
    )
    outgoing = finish_action(updated) if not quizzes else {}
    async with atomic():
        await persist(user_id, previous, updated)
    return await action_view(user_id, updated, outgoing)


async def select_end_candidate(user_id, save):
    """出題元が失われた専門分野や未開放装備枠も飛ばして次の行を判定する."""
    search = save.model_copy(deep=True)
    catalog = await get_game_catalog()
    slots = (await gambit_capacity(user_id)).slots
    search.gambits = search.gambits[:slots]
    equipped = [s for s in search.specialties if s.equipped]
    allowed = (await specialty_capacity(user_id))["slots"] if equipped else 0
    for specialty in equipped[allowed:]:
        specialty.equipped = False
    while selected := choose_action(search, catalog, slots=slots):
        skill, quizzes = selected.skill, []
        if selected.specialty_id:
            specialty = next(
                s for s in search.specialties if s.id == selected.specialty_id
            )
            resources = {
                to_uuid(r.resource_id).hex: r
                for r in await fetch_resource_growth(user_id)
            }
            skill = strengthened(
                skill,
                specialty,
                resources,
                selected.specialty_use.power_coefficient,
            )
            needs_quiz = selected.specialty_use.quiz_required
            if needs_quiz:
                pools = {
                    p["resource_id"]: p["quiz_ids"]
                    for p in await specialty_pools(user_id, GameState(save=search))
                }
                if any(not pools.get(r) for r in specialty.resource_ids):
                    search.gambits = [
                        r for r in search.gambits if r.definition_id != specialty.id
                    ]
                    continue
                # One action is one question, even after resources are fused.
                quizzes = [choice(pools[choice(specialty.resource_ids)])]
                for quiz_id in quizzes:
                    await expand_quiz_chain(user_id, to_uuid(quiz_id))
        return selected, skill, quizzes
    return None, None, []


def prepare_counter_damage(state, context, target_ids=None) -> int:
    """反撃用の一体分の通常攻撃を、出題時にサーバーで固定する。."""
    from .damage import enemy_damage  # noqa: PLC0415
    from .scheduled_combat import incoming_attack  # noqa: PLC0415

    marker = state.save.battle
    targets = target_ids or marker.enemies
    enemy = next((e for e in context.enemies if e.id in targets), context.enemies[0])
    return incoming_attack(
        state,
        context,
        enemy,
        context.default_attack or normal_attack(),
        enemy_damage,
    )


def begin_action(
    save,
    selected,
    skill,
    quizzes: list[str],
    enemy_hp: dict,
    *,
    counter_damage=0,
    context=None,
) -> None:
    """実戦と模擬戦で一回分の消費・出題状態を共通にする."""
    if selected.item_id:
        consume_use(save.economy, selected.item_id, selected.item_max_uses)
    else:
        save.run.mp -= skill.mp_cost
    save.battle.endAction = {
        "id": uuid4().hex,
        "name": selected.name,
        "skill": skill.model_dump(),
        "target_ids": selected.target_ids,
        "details": describe_action(
            selected,
            skill,
            save.battle,
            {
                "player": "あなた",
                **({e.id: e.name for e in context.enemies} if context else {}),
            },
        ).model_dump(),
        "quiz_ids": quizzes,
        "index": 0,
        "correct": 0,
        "counter_damage": counter_damage,
        "equipment_stats": equipped_stats(
            save.allocation,
            context.balance,
            save.economy,
        )
        if context
        else None,
        "base_stats": save.allocation.stats(context.balance) if context else None,
        "weaknesses": {enemy.id: enemy.weaknesses.copy() for enemy in context.enemies}
        if context
        else {},
        "weakness_multiplier": context.balance.weakness_multiplier if context else 1.5,
        "resistances": {enemy.id: enemy.resistances.copy() for enemy in context.enemies}
        if context
        else {},
        "resistance_multiplier": context.balance.resistance_multiplier
        if context
        else 0.5,
        "enabled_elements": context.enabled_elements if context else None,
        "quiz_seconds": selected.specialty_use.quiz_seconds
        if selected.specialty_use
        else 30,
        "wrong_multiplier": selected.specialty_use.wrong_multiplier
        if selected.specialty_use
        else 0,
        "deadline": int(time() * 1000)
        + (selected.specialty_use.quiz_seconds if selected.specialty_use else 30)
        * 1000,
        "enemy_hp": {
            e: max(1, min(100000, enemy_hp.get(e, 100000))) for e in save.battle.enemies
        },
    }


async def answer_end_action(user_id, body: EndActionAnswer) -> dict:
    """各問題を一度保存し、全リソースの回答後に効果を一度適用する."""
    previous = await read_state(user_id)
    receipt = previous.save.endActionReceipt or {}
    if receipt.get("action_id") == body.action_id:
        return await action_view(user_id, previous, receipt.get("outgoing_damage"))
    action = previous.save.battle.endAction if previous.save.battle else None
    if not action or action["id"] != body.action_id:
        raise HTTPException(409, "専用クイズの状態が変わっています。")
    if action["index"] > body.index:
        return await action_view(user_id, previous)
    if action["index"] != body.index:
        raise HTTPException(409, "専用クイズの回答順が変わっています。")
    quiz_id = action["quiz_ids"][body.index]
    chain = await expand_quiz_chain(user_id, to_uuid(quiz_id))
    quiz = chain.quizzes[0].readable
    if body.selected is not None and not set(body.selected) <= set(quiz.options):
        raise HTTPException(422, "存在しない選択肢です。")
    correct = (
        body.selected is not None
        and int(time() * 1000) <= action["deadline"]
        and quiz.is_correct(body.selected)
    )
    updated = previous.model_copy(deep=True)
    next_action = updated.save.battle.endAction
    next_action["correct"] += int(correct)
    next_action["index"] += 1
    next_action["deadline"] = (
        int(time() * 1000)
        + next_action.get("quiz_seconds", next_action["skill"].get("quiz_seconds", 30))
        * 1000
    )
    outgoing = (
        finish_action(updated)
        if next_action["index"] == len(next_action["quiz_ids"])
        else {}
    )
    notifications = []
    async with atomic():
        await persist(user_id, previous, updated)
        if body.selected is not None:
            _, notifications = await create_answer_in_transaction(
                to_uuid(quiz_id),
                body.selected,
                user_id,
                is_correct=correct,
            )
    await dispatch_saved_notifications(user_id, notifications)
    return await action_view(user_id, updated, outgoing)


def sync_scheduled_damage(marker, outgoing, killed) -> None:
    """Keep specialty damage in the authoritative CTB HP and forecast."""
    if not marker.schedule:
        return
    from .action_queue import predict, refresh_speeds  # noqa: PLC0415

    for identity, damage in outgoing.items():
        marker.schedule.enemy_hp[identity] = max(
            0,
            marker.schedule.enemy_hp[identity] - damage,
        )
    refresh_speeds(
        marker.schedule,
        {
            key: speed
            for key, speed in marker.schedule.speeds.items()
            if key not in killed
        },
    )
    marker.schedule.queue = predict(marker.schedule)


def resolved_end_effect(action, component=None):
    """新しい一問形式と、進行中の旧複数問形式の効果を選ぶ。."""
    skill = component or parse_skill(action["skill"])
    total = len(action["quiz_ids"])
    rate = action["correct"] / total if total else 1
    if total <= 1 and "on_wrong" in action["skill"]:
        effect = answer_effect(skill, rate == 1)
        return effect or skill, int(effect is not None), effect is not None
    wrong = action.get("wrong_multiplier", action["skill"].get("wrong_multiplier", 0))
    factor = wrong + (1 - wrong) * rate
    applies = rate == 1 if skill.effect in {"buff", "debuff", "ailment"} else factor > 0
    return skill, factor, applies


def apply_end_effect(state, action, skill, factor, applies):
    """消費せずに一効果を適用する。撃破済み対象は次の効果から外れる."""
    marker, run = state.save.battle, state.save.run
    targets = (
        ["player"]
        if skill.target == "self"
        else skill_targets(skill, marker.enemies, action.get("target_ids"))
    )
    targets = (
        [target for target in targets if effect_activates(skill, marker, target)]
        if applies
        else targets
    )
    outgoing = {}
    if skill.effect in {"heal_hp", "heal_mp", "damage_mp"}:
        apply_resource_effect(
            state.save,
            skill.model_copy(update={"amount": floor(skill.amount * factor)}),
            affinities=action,
            target_ids=action.get("target_ids"),
        )
    elif skill.effect == "attack" and skill.target == "self" and targets:
        damage = elemental_damage(
            floor(
                (
                    effect_amount(skill, run.maxHp)
                    + getattr(run, attack_stat(skill)) * skill.multiplier
                )
                * factor,
            ),
            skill,
            marker,
            "player",
            "player",
            economy=state.save.economy,
            enabled_elements=action.get("enabled_elements"),
        )
        run.hp = max(0, run.hp - damage)
        if action.get("equipment_stats"):
            wear_defensive_equipment(
                state.save,
                damage,
                action["equipment_stats"],
                action.get("base_stats"),
            )
    elif skill.effect == "attack" and skill.target != "self":
        outgoing = {
            e: min(
                action["enemy_hp"][e],
                elemental_damage(
                    floor(
                        (
                            effect_amount(skill, action["enemy_hp"][e])
                            + getattr(run, attack_stat(skill)) * skill.multiplier
                        )
                        * factor,
                    ),
                    skill,
                    marker,
                    "player",
                    e,
                    weaknesses=action.get("weaknesses", {}).get(e, []),
                    resistances=action.get("resistances", {}).get(e, []),
                    resistance_multiplier=action.get("resistance_multiplier", 0.5),
                    weakness_multiplier=action.get("weakness_multiplier", 1.5),
                    economy=state.save.economy,
                    enabled_elements=action.get("enabled_elements"),
                ),
            )
            for e in targets
        }
        killed = [
            e for e, damage in outgoing.items() if damage >= action["enemy_hp"][e]
        ]
        sync_scheduled_damage(marker, outgoing, killed)
        marker.enemies = [e for e in marker.enemies if e not in killed]
        run.kills += 0 if marker.bossLevel else len(killed)
        state.save.economy.gold += len(killed) * (marker.region + 1) * GOLD_PER_LEVEL
        for identity, damage in outgoing.items():
            action["enemy_hp"][identity] = max(0, action["enemy_hp"][identity] - damage)
    elif applies and skill.effect in {
        "buff",
        "debuff",
        "ailment",
    }:
        for target in targets:
            add_effect(marker, skill, target)
            marker.effects[-1].starts_turn = marker.turn
            marker.effects[-1].expires_turn = effect_expiry(
                marker.turn,
                skill.duration_turns,
            )
    return outgoing, targets


def finish_action(state: GameState) -> dict[str, int]:
    """同じ正誤で全効果を処理し、消費・演出・反撃は一回にまとめる."""
    marker, run = state.save.battle, state.save.run
    action = marker.endAction
    original = parse_skill(action["skill"])
    outgoing, targets, applies = {}, [], False
    for component in skill_effects(original):
        skill, factor, applied = resolved_end_effect(action, component)
        dealt, affected = apply_end_effect(state, action, skill, factor, applied)
        for identity, amount in dealt.items():
            outgoing[identity] = outgoing.get(identity, 0) + amount
        targets.extend(identity for identity in affected if identity not in targets)
        applies = applies or (applied and bool(affected))
    skill = original
    total = len(action["quiz_ids"])
    if total:
        state.save.quizFeedback = (
            ("正解" if action["correct"] else "不正解")
            if total == 1
            else f"{action['correct']}/{total}問正解"
        )
    state.save.battleFeedback = f"{action['name']} · {'発動' if applies else '不発'}"
    state.save.battleActionDetails = (
        BattleActionDetails.model_validate(action["details"])
        if action.get("details")
        else None
    )
    marker.endAction = None
    state.save.endActionReceipt = {
        "battle_id": marker.id,
        "turn": marker.turn,
        "action_id": action["id"],
        "outgoing_damage": outgoing,
        "sound": skill.sound if applies else "none",
        "effect": skill.effect,
        "target": skill.target,
        "target_ids": targets,
        "ailment": getattr(skill, "ailment", None),
        "animation": skill.animation if applies else "none",
    }
    apply_end_counter(state, action, skill)
    if not marker.enemies:
        if marker.bossLevel:
            resource = to_uuid(run.resourceId).hex
            levels = state.save.bossClears.setdefault(resource, [])
            if marker.bossLevel not in levels:
                levels.append(marker.bossLevel)
        run.phase = (
            "cleared"
            if not marker.bossLevel and run.kills >= ENEMIES_TO_CLEAR
            else "rest"
            if run.moves >= MOVES_PER_EVENT
            else "path"
        )
        if run.phase == "cleared":
            state.save.clears[run.resourceId] = (
                state.save.clears.get(run.resourceId, 0) + 1
            )
        state.save.battle = None
    if run.hp == 0:
        finish_defeat(state)
    sync_bubble(run, state.save.battle)
    sync_speed_effects(state.save.battle)
    return outgoing


def apply_end_counter(state, action, skill):
    """失敗時の反撃を一度反映し、戦闘不能なら入口へ戻す。."""
    if not action["quiz_ids"] or action["correct"] or not has_counter(skill):
        return
    damage = action.get("counter_damage", 0)
    run = state.save.run
    run.hp = max(0, run.hp - damage)
    if action.get("equipment_stats"):
        broken = wear_defensive_equipment(
            state.save,
            damage,
            action["equipment_stats"],
            action.get("base_stats"),
        )
    else:
        broken = (
            damage > 0
            and equipment_wears_on_damage(state.save.economy)
            and wear_weapon(state.save.economy, 1, defensive=True)
        )
    if broken:
        state.save.battleFeedback += " · 防具が壊れた"
    state.save.endActionReceipt["damage"] = damage
    if run.hp == 0:
        finish_defeat(state)


def finish_defeat(state):
    """自傷でも反撃でも戦闘不能になったら入口へ戻す。."""
    run = state.save.run
    run.phase = "defeated"
    dungeon = state.save.maps.get(run.resourceId)
    if dungeon:
        dungeon.current = "@entrance"
    state.save.battle = None
