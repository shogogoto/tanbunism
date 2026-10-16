"""ボス攻略済みの知識から呼び出す、実際の復習に連動したスキル."""

import json
from math import ceil, log1p
from secrets import choice
from time import time
from typing import Annotated
from uuid import uuid4

from fastapi import HTTPException
from neomodel import adb
from pydantic import BaseModel, Field

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.notification.usecase import dispatch_saved_notifications
from tanbun.feature.quiz.answering.repo import create_answer_in_transaction
from tanbun.feature.quiz.chain.usecase import expand_quiz_chain

from .combat import (
    TurnRequest,
    apply_turn_result,
    award_kill_gold,
    combat_context,
    persist,
)
from .damage import enemy_damage, player_damage
from .inventory import equipped_stats
from .state import GameState, SpecialtySkill, read_state


class SkillRequest(BaseModel):
    """結果画面にいる戦闘を指定する."""

    battle_id: str = Field(max_length=64)
    turn: int = Field(ge=0)


class SkillAnswer(BaseModel):
    """未回答はNone、回答した空選択は[]として区別する."""

    skill_id: str = Field(max_length=64)
    selected: list[str] | None = Field(default=None, max_length=100)
    enemy_hp: dict[str, Annotated[int, Field(ge=1, le=100000)]] = Field(
        default_factory=dict,
    )
    retreat: bool = False


async def specialty_pools(user_id: UUIDy, state: GameState) -> list[dict]:
    """ボス攻略済みリソースの、本人が利用可能な作成済みクイズを使う."""
    unlocks = [
        {"resource": to_uuid(r).hex, "level": max(levels)}
        for r, levels in state.save.bossClears.items()
        if levels
    ]
    if not unlocks:
        return []
    rows, _ = await adb.cypher_query(
        """UNWIND $unlocks AS unlock
        MATCH (u:User {uid:$uid})-[:LEARN]->(q)
        MATCH (r:Resource {uid:unlock.resource})-[:PARENT*0..]->()-[:OWNED]->(u)
        MATCH (q)-[:QUIZ_TARGET]->(:Sentence {resource_uid:unlock.resource})
        WHERE NOT EXISTS { MATCH (q)-[:BROKEN_BY]->() }
        RETURN r.uid, coalesce(r.title, '専門分野'), collect(DISTINCT q.uid)""",
        {"uid": to_uuid(user_id).hex, "unlocks": unlocks},
    )
    return [
        {"resource_id": to_uuid(r).hex, "name": n, "quiz_ids": qs}
        for r, n, qs in rows
        if qs
    ]


async def start_skill(user_id: UUIDy, body: SkillRequest) -> dict:
    """ターン間に一度だけ。再送では同じ出題を返し、MPを再消費しない."""
    previous = await read_state(user_id)
    marker, run = previous.save.battle, previous.save.run
    if (
        not marker
        or not run
        or not run.hp
        or (marker.id, marker.turn) != (body.battle_id, body.turn)
        or not previous.save.battleFeedback
    ):
        raise HTTPException(409, "回答結果の処理後に専門分野を呼び出してください。")
    if marker.skill:
        return await skill_session(user_id, previous)
    if marker.skillUsed:
        raise HTTPException(422, "このターンでは専門分野を使用済みです。")
    context = await combat_context(user_id, previous)
    if (
        min(
            run.mp,
            equipped_stats(
                previous.save.allocation,
                context.balance,
                previous.save.economy,
            )["maxMp"],
        )
        < context.balance.skill_mp_cost
    ):
        raise HTTPException(422, "MPが不足しています。")
    pools = await specialty_pools(user_id, previous)
    if not pools:
        raise HTTPException(422, "ボスを倒して専門分野を開放してください。")
    source = choice(pools)
    quiz_id = choice(source["quiz_ids"])
    # Validate the selected real quiz before spending MP.
    chain = await expand_quiz_chain(user_id, to_uuid(quiz_id))
    growth = await fetch_resource_growth(user_id, only_resource=source["resource_id"])
    terms = growth[0].term_count if growth else 0
    updated = previous.model_copy(deep=True)
    updated.save.run.mp = (
        min(
            run.mp,
            equipped_stats(
                previous.save.allocation,
                context.balance,
                previous.save.economy,
            )["maxMp"],
        )
        - context.balance.skill_mp_cost
    )
    updated.save.battle.skillUsed = True
    updated.save.battle.skill = SpecialtySkill(
        id=uuid4().hex,
        resourceId=source["resource_id"],
        name=source["name"],
        quizId=quiz_id,
        attack=min(
            100000,
            max(1, ceil(log1p(terms) * context.balance.skill_term_attack)),
        ),
        deadline=int(time() * 1000) + run.answerSeconds * 1000,
    )
    async with atomic():
        await persist(user_id, previous, updated)
    return {
        "state": updated,
        "skill": updated.save.battle.skill,
        "quiz": chain.quizzes[0].readable,
    }


async def skill_session(user_id: UUIDy, state: GameState) -> dict:
    """選択肢・持ち時間は既存クイズをそのまま使う."""
    skill = state.save.battle.skill
    chain = await expand_quiz_chain(user_id, to_uuid(skill.quizId))
    return {"state": state, "skill": skill, "quiz": chain.quizzes[0].readable}


async def resolve_skill(user_id: UUIDy, body: SkillAnswer) -> dict:
    """正誤・消費済みMP・ゲーム結果と回答履歴を一度だけ精算する."""
    receipt = await read_skill_receipt(user_id)
    if receipt and receipt["id"] == body.skill_id:
        return {**receipt, "state": await read_state(user_id)}
    previous = await read_state(user_id)
    marker = previous.save.battle
    if (
        not marker
        or not previous.save.run
        or not marker.skill
        or marker.skill.id != body.skill_id
    ):
        raise HTTPException(409, "専門分野の出題が更新されました。")
    skill = marker.skill
    correct = await evaluate_skill(user_id, skill, body)
    context = await combat_context(user_id, previous)
    outgoing, incoming, defeated = skill_damage(
        previous,
        body,
        context,
        correct=correct,
    )
    updated = apply_skill_result(
        previous,
        body,
        context,
        outgoing,
        incoming,
        defeated=defeated,
        correct=correct,
    )
    receipt = {
        "id": body.skill_id,
        "correct": correct,
        "outgoing_damage": outgoing,
        "damage": incoming,
    }
    notifications = []
    async with atomic():
        await persist(user_id, previous, updated)
        if body.selected is not None:
            _answer, notifications = await create_answer_in_transaction(
                to_uuid(skill.quizId),
                body.selected,
                user_id,
                is_correct=correct,
            )
        await adb.cypher_query(
            "MATCH (u:User {uid:$uid}) SET u.game_skill_receipt=$receipt",
            {"uid": to_uuid(user_id).hex, "receipt": json.dumps(receipt)},
        )
    await dispatch_saved_notifications(user_id, notifications)
    return {**receipt, "state": updated}


def skill_damage(
    previous: GameState,
    body: SkillAnswer,
    context,
    *,
    correct: bool,
) -> tuple[dict, int, list[str]]:
    """敵HPはブラウザの値を受け、スキルのダメージ自体は共通の式で計算."""
    marker = previous.save.battle
    if not correct:
        return (
            {},
            enemy_damage(
                marker.skill.attack,
                previous.save.run.defense,
                context.balance,
            ),
            [],
        )
    enemies = {e.id: e for e in context.enemies}
    if (
        not set(marker.enemies) <= enemies.keys()
        or set(body.enemy_hp) != set(marker.enemies)
        or any(hp > enemies[e].hp for e, hp in body.enemy_hp.items())
    ):
        raise HTTPException(422, "現在の敵HPを取得できませんでした。")
    outgoing = {
        e: player_damage(marker.skill.attack, context.balance) for e in marker.enemies
    }
    return outgoing, 0, [e for e in marker.enemies if outgoing[e] >= body.enemy_hp[e]]


async def read_skill_receipt(user_id: UUIDy) -> dict | None:
    """通常ターンとは別の受領記録を保持する."""
    rows, _ = await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) RETURN u.game_skill_receipt",
        {"uid": to_uuid(user_id).hex},
    )
    return json.loads(rows[0][0]) if rows and rows[0][0] else None


async def evaluate_skill(
    user_id: UUIDy,
    skill: SpecialtySkill,
    body: SkillAnswer,
) -> bool:
    """学習側と共通の出題・正誤判定を使う."""
    if body.selected is None:
        return False
    chain = await expand_quiz_chain(user_id, to_uuid(skill.quizId))
    quiz = chain.quizzes[0].readable
    if body.selected is not None and not set(body.selected) <= set(quiz.options):
        raise HTTPException(422, "クイズに存在しない選択肢です。")
    return body.selected is not None and quiz.is_correct(body.selected)


def apply_skill_result(
    previous: GameState,
    body: SkillAnswer,
    context,
    outgoing: dict,
    incoming: int,
    *,
    defeated: list[str],
    correct: bool,
) -> GameState:
    """通常戦闘と同じ敗北・攻略・ゴールド処理につなぐ."""
    marker = previous.save.battle
    request = TurnRequest(
        battle_id=marker.id,
        turn=marker.turn,
        defeated=defeated,
        retreat=body.retreat,
    )
    stats = equipped_stats(
        previous.save.allocation,
        context.balance,
        previous.save.economy,
    )
    updated = apply_turn_result(
        previous,
        request,
        dict.fromkeys(marker.enemies, correct),
        incoming,
        stats,
    )
    if updated.save.battle:
        updated.save.battle.skill = None
    award_kill_gold(updated, request, marker.region)
    label = "正解" if correct else "未回答" if body.selected is None else "不正解"
    effect = (
        "全体攻撃 "
        f"{min(outgoing.values(), default=0)}〜{max(outgoing.values(), default=0)}"
        if correct
        else f"HP -{incoming}"
    )
    unlocked = "専門分野開放" in (updated.save.battleFeedback or "")
    updated.save.battleFeedback = f"{marker.skill.name} · {label} · {effect}" + (
        " · 専門分野開放" if unlocked else ""
    )
    gold = updated.save.economy.gold - previous.save.economy.gold
    if gold:
        updated.save.battleFeedback += f" · +{gold} G"
    return updated
