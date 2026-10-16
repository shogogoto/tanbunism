"""探索を止めない節目のボス戦。初回勝利で専門分野を開放する."""

from secrets import choice
from time import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.quiz.repo.restore import restore_quiz_sources

from .combat import MOVES_PER_EVENT, combat_context, persist
from .gambits import prepare_action
from .inventory import equipped_stats
from .population import _validate_quiz_ids, get_or_prepare_region_pool
from .state import BattleMarker, GameState, read_state


class BossRequest(BaseModel):
    """達成度5刻みのボスを選んで挑む."""

    revision: int = Field(ge=0)
    level: int = Field(ge=5, le=20, multiple_of=5)


async def start_boss(user_id: UUIDy, body: BossRequest) -> GameState:
    """1歩消費し、固定母集団の最新5領域からボスを作る."""
    previous = await read_state(user_id)
    run = previous.save.run
    if (
        previous.revision != body.revision
        or previous.save.battleFeedback
        or previous.save.battle
    ):
        raise HTTPException(409, "冒険状態が変更されました。")
    if (
        not run
        or run.phase not in {"path", "cleared"}
        or not run.hp
        or run.moves >= MOVES_PER_EVENT
    ):
        raise HTTPException(422, "HPと歩数を回復してから挑戦してください。")
    dungeon = previous.save.maps.get(run.resourceId)
    if not dungeon or len(dungeon.places) < body.level * 5:
        raise HTTPException(422, "このボスの達成度に到達していません。")
    pool = await get_or_prepare_region_pool(user_id, run.resourceId, body.level)
    quiz_ids = pool["quiz_ids"][-25:]
    if not pool["ready"] or not await _validate_quiz_ids(
        user_id,
        run.resourceId,
        quiz_ids,
    ):
        raise HTTPException(409, "ボスのクイズを準備中です。")
    updated = previous.model_copy(deep=True)
    content = updated.save.content or {"knowledge": [], "quizzes": []}
    existing = {to_uuid(q["quiz_id"]).hex for q in content.get("quizzes", [])}
    sources = await restore_quiz_sources([q for q in quiz_ids if q not in existing])
    content.setdefault("quizzes", []).extend(
        s.to_readable().model_dump(mode="json") for s in sources
    )
    updated.save.content = content
    identity = f"boss:{to_uuid(run.resourceId).hex}:{body.level}"
    updated.save.battle = BattleMarker(
        id=uuid4().hex,
        region=body.level - 1,
        checkpoint=dungeon.current,
        enemies=[identity],
        bossLevel=body.level,
        bossQuizIds=quiz_ids,
    )
    context = await combat_context(user_id, updated)
    enemy = next((e for e in context.enemies if e.id == identity), None)
    if not enemy:
        raise HTTPException(409, "ボスのクイズを取得できませんでした。")
    updated.save.battle.quizIndices = {identity: choice(enemy.quizIndexes)}
    stats = equipped_stats(
        updated.save.allocation,
        context.balance,
        updated.save.economy,
    )
    for key, value in stats.items():
        setattr(updated.save.run, key, value)
    updated.save.run.hp = min(run.hp, stats["maxHp"])
    updated.save.run.mp = stats["maxMp"]
    updated.save.run.moves += 1
    if run.phase == "cleared":
        updated.save.run.kills = 0
    updated.save.run.phase = "battle"
    updated.save.run.answerDeadline = int(time() * 1000) + stats["answerSeconds"] * 1000
    await prepare_action(updated.save, user_id)
    async with atomic():
        await persist(user_id, previous, updated)
    return updated
