"""Administrative practice sessions: read sources, never persist any outcome."""

from random import Random
from time import time
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from tanbun.feature.quiz.domain.domain import ReadableQuiz

from .admin import _dungeon_content, _legacy_pool_ids
from .balance import Allocation, GameBalance
from .combat import (
    CombatContext,
    TurnRequest,
    apply_turn_result,
    combat_context,
    select_encounter,
)
from .damage import damage_range
from .roster import assign_enemy_quizzes, enemy_identity
from .state import BattleMarker, GameSave, GameState, read_state


class PracticeRequest(BaseModel):
    """Draft settings and existing dungeon content; no generation or writes."""

    user_id: UUID
    player_user_id: UUID | None = None
    resource_id: UUID
    level: int = Field(ge=1, le=20)
    balance: GameBalance
    allocation: Allocation | None = None
    seed: int = Field(default=1, ge=0, le=2**31 - 1)


class PracticeSession(BaseModel):
    """Only the browser owns this state; requests are stateless calculators."""

    state: GameState
    context: CombatContext
    seed: int = Field(ge=0, le=2**31 - 1)
    server_now: int


class PracticeTurn(BaseModel):
    """A sealed practice turn, isolated from live battle endpoints."""

    session: PracticeSession
    turn: TurnRequest


def _random(seed: int, turn: int = -1) -> Random:
    return Random(f"practice:{seed}:{turn}")  # noqa: S311 - reproducible simulation


async def prepare_practice(body: PracticeRequest) -> PracticeSession:
    """Read a saved population and rebuild only the disposable simulation copy."""
    source = await read_state(body.user_id)
    content = _dungeon_content(source, body.resource_id.hex)
    if not content:
        raise HTTPException(
            409,
            "保存済みのダンジョンがありません。まず冒険してください。",
        )
    region = body.level - 1
    pool = (content.get("regionQuizPools") or {}).get(str(region))
    if not pool:
        pool = _legacy_pool_ids(
            (content.get("regionEnemies") or {}).get(str(region), []),
            content.get("quizzes", []),
        )
    if not pool:
        raise HTTPException(
            409,
            "この領域の母集団がありません。準備済み領域を選んでください。",
        )
    source = source.model_copy(deep=True)
    content = _dungeon_content(source, body.resource_id.hex)
    pool_ids = {str(uid).replace("-", "").lower() for uid in pool}
    content = {
        "knowledge": [],
        "quizzes": [
            quiz
            for quiz in content.get("quizzes", [])
            if str(quiz.get("quiz_id", "")).replace("-", "").lower() in pool_ids
        ],
    }
    rng = _random(body.seed)
    groups = assign_enemy_quizzes(
        list(range(len(content["quizzes"]))),
        body.balance,
        region,
        rng=rng,
    )
    content["regionEnemies"] = {
        str(region): [
            {
                "id": enemy_identity(body.resource_id.hex, region, i),
                "name": f"領域 {body.level}の敵 {i + 1}",
                "quizIndex": group[0],
                "quizIndexes": group,
            }
            for i, group in enumerate(groups)
        ],
    }
    run = source.save.run
    if not run or run.resourceId.replace("-", "").lower() != body.resource_id.hex:
        run = next(
            dungeon.run
            for key, dungeon in source.save.dungeons.items()
            if key.replace("-", "").lower() == body.resource_id.hex
        )
    player_source = source
    if (
        body.allocation is None
        and body.player_user_id is not None
        and body.player_user_id != body.user_id
    ):
        player_source = await read_state(body.player_user_id)
    allocation = body.allocation or player_source.save.allocation
    stats = allocation.stats(body.balance)
    run = run.model_copy(
        update={
            **stats,
            "hp": stats["maxHp"],
            "moves": 0,
            "kills": 0,
            "quizCursor": 0,
            "readIds": [],
            "phase": "battle",
        },
    )
    state = GameState(save=GameSave(run=run, allocation=allocation, content=content))
    context = await combat_context(body.user_id, state, balance=body.balance)
    chosen = select_encounter(
        context.enemies,
        body.balance,
        region,
        pick=rng.choice,
        draw=rng.randrange,
    )
    if not chosen:
        raise HTTPException(409, "有効なクイズがありません。母集団を更新してください。")
    context.enemies = chosen
    state.save.battle = BattleMarker(
        id=f"practice-{body.seed}",
        region=region,
        enemies=[enemy.id for enemy in chosen],
        quizIndices={enemy.id: rng.choice(enemy.quizIndexes) for enemy in chosen},
    )
    now = int(time() * 1000)
    state.save.run.answerDeadline = now + stats["answerSeconds"] * 1000
    return PracticeSession(state=state, context=context, seed=body.seed, server_now=now)


def practice_turn(body: PracticeTurn) -> dict:
    """Reuse correctness, damage and turn transitions without saving answers/XP."""
    session, turn = body.session, body.turn
    state, marker = session.state, session.state.save.battle
    if (
        not marker
        or not state.save.run
        or (marker.id, marker.turn) != (turn.battle_id, turn.turn)
    ):
        raise HTTPException(422, "模擬戦のターンが一致しません。")
    if not set(turn.answers) <= set(marker.enemies) or not set(turn.defeated) <= set(
        marker.enemies,
    ):
        raise HTTPException(422, "模擬戦に存在しない敵です。")
    enemies = {enemy.id: enemy for enemy in session.context.enemies}
    quizzes = (state.save.content or {}).get("quizzes", [])
    if any(
        enemy not in enemies
        or enemy not in marker.quizIndices
        or not 0 <= marker.quizIndices[enemy] < len(quizzes)
        for enemy in marker.enemies
    ):
        raise HTTPException(422, "模擬戦の敵またはクイズが見つかりません。")
    results = {}
    for enemy_id, selected in turn.answers.items():
        try:
            quiz = ReadableQuiz.model_validate(quizzes[marker.quizIndices[enemy_id]])
        except ValidationError as error:
            raise HTTPException(
                422,
                "模擬戦のクイズを読み取れません。再開してください。",
            ) from error
        if not set(selected) <= set(quiz.options):
            raise HTTPException(422, "クイズに存在しない選択肢です。")
        results[enemy_id] = quiz.is_correct(selected)
    if any(not results.get(enemy) for enemy in turn.defeated):
        raise HTTPException(422, "未正解の敵は撃破できません。")
    stats = state.save.allocation.stats(session.context.balance)
    rng = _random(session.seed, marker.turn)
    damage = 0
    for enemy in marker.enemies:
        if not results.get(enemy):
            low, _, high = damage_range(
                enemies[enemy].attack,
                stats["defense"],
                session.context.balance,
            )
            damage += rng.randint(low, high)
    updated = apply_turn_result(state, turn, results, damage, stats)
    return {"state": updated, "results": results, "damage": damage}


def next_practice_turn(session: PracticeSession) -> GameState:
    """Start the next disposable turn only after reading its feedback."""
    state = session.state.model_copy(deep=True)
    marker = state.save.battle
    if not marker or not state.save.run or not state.save.battleFeedback:
        raise HTTPException(422, "模擬戦の次のターンを開始できません。")
    rng = _random(session.seed, marker.turn)
    enemies = {enemy.id: enemy for enemy in session.context.enemies}
    if not set(marker.enemies) <= enemies.keys():
        raise HTTPException(422, "模擬戦の敵が見つかりません。")
    marker.quizIndices = {
        enemy: rng.choice(enemies[enemy].quizIndexes) for enemy in marker.enemies
    }
    state.save.battleFeedback = None
    state.save.run.answerDeadline = (
        int(time() * 1000)
        + session.state.save.allocation.stats(session.context.balance)["answerSeconds"]
        * 1000
    )
    return state
