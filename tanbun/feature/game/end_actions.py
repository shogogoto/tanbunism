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

from .catalog import get_game_catalog, parse_skill
from .combat import ENEMIES_TO_CLEAR, MOVES_PER_EVENT, combat_context, persist
from .economy import GOLD_PER_LEVEL
from .effects import add_effect, effect_amount, sync_bubble
from .gambit_slots import gambit_capacity
from .gambits import choose_action
from .inventory import consume_use
from .skill_outcomes import answer_effect, attack_stat, normal_attack, skill_targets
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
    begin_action(
        updated.save,
        selected,
        skill,
        quizzes,
        marker.schedule.enemy_hp if marker.schedule else body.enemy_hp,
        counter_damage=prepare_counter_damage(
            updated,
            await combat_context(user_id, updated),
        )
        if quizzes and skill.on_wrong.type == "counter"
        else 0,
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


def prepare_counter_damage(state, context) -> int:
    """反撃用の一体分の通常攻撃を、出題時にサーバーで固定する。."""
    from .damage import enemy_damage  # noqa: PLC0415
    from .scheduled_combat import incoming_attack  # noqa: PLC0415

    enemy = next(e for e in context.enemies if e.id == state.save.battle.enemies[0])
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
        "quiz_ids": quizzes,
        "index": 0,
        "correct": 0,
        "counter_damage": counter_damage,
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


def resolved_end_effect(action):
    """新しい一問形式と、進行中の旧複数問形式の効果を選ぶ。."""
    skill = parse_skill(action["skill"])
    total = len(action["quiz_ids"])
    rate = action["correct"] / total if total else 1
    if total <= 1 and "on_wrong" in action["skill"]:
        effect = answer_effect(skill, rate == 1)
        return effect or skill, int(effect is not None), effect is not None
    wrong = action.get("wrong_multiplier", action["skill"].get("wrong_multiplier", 0))
    factor = wrong + (1 - wrong) * rate
    applies = rate == 1 if skill.effect in {"buff", "debuff", "ailment"} else factor > 0
    return skill, factor, applies


def finish_action(state: GameState) -> dict[str, int]:
    """一問の正誤に応じた効果を一度だけ適用する。."""
    marker, run = state.save.battle, state.save.run
    action = marker.endAction
    skill, factor, applies = resolved_end_effect(action)
    total = len(action["quiz_ids"])
    targets = (
        ["player"] if skill.target == "self" else skill_targets(skill, marker.enemies)
    )
    outgoing = {}
    if skill.effect == "heal_hp":
        run.hp = min(
            run.maxHp,
            run.hp + floor(effect_amount(skill, run.maxHp) * factor),
        )
    elif skill.effect == "heal_mp":
        run.mp = min(
            run.maxMp,
            run.mp + floor(effect_amount(skill, run.maxMp) * factor),
        )
    elif skill.effect == "attack":
        outgoing = {
            e: min(
                action["enemy_hp"][e],
                floor(
                    (
                        effect_amount(skill, action["enemy_hp"][e])
                        + getattr(run, attack_stat(skill)) * skill.multiplier
                    )
                    * factor,
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
    elif applies and skill.effect in {
        "buff",
        "debuff",
        "ailment",
    }:
        for target in targets:
            add_effect(marker, skill, target)
            marker.effects[-1].starts_turn = marker.turn
            marker.effects[-1].expires_turn = marker.turn + skill.duration_turns
    state.save.battleFeedback = (
        f"{action['name']} · {action['correct']}/{total}問正解"
        if total
        else f"{action['name']} · 発動"
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
    sync_bubble(run, state.save.battle)
    return outgoing


def apply_end_counter(state, action, skill):
    """失敗時の反撃を一度反映し、戦闘不能なら入口へ戻す。."""
    if not action["quiz_ids"] or action["correct"] or skill.on_wrong.type != "counter":
        return
    damage = action.get("counter_damage", 0)
    run = state.save.run
    run.hp = max(0, run.hp - damage)
    state.save.endActionReceipt["damage"] = damage
    if run.hp == 0:
        run.phase = "defeated"
        dungeon = state.save.maps.get(run.resourceId)
        if dungeon:
            dungeon.current = "@entrance"
        state.save.battle = None
