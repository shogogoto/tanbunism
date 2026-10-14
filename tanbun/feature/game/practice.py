"""Administrative practice sessions: read sources, never persist any outcome."""

from random import Random
from time import time
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from tanbun.feature.quiz.domain.domain import ReadableQuiz

from .admin import EnemyBalanceSimulationResult, _dungeon_content, _legacy_pool_ids
from .balance import Allocation, GameBalance
from .combat import (
    CombatContext,
    EnemyStats,
    TurnRequest,
    apply_turn_result,
    combat_context,
    select_encounter,
)
from .damage import damage_range
from .inventory import Economy, equipped_stats
from .roster import assign_enemy_quizzes, enemy_identity
from .state import BattleMarker, GameSave, GameState, Run, read_state


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


class SimulatedPracticeRequest(BaseModel):
    """Use an enemy trial snapshot and the current draft player/damage settings."""

    simulation: EnemyBalanceSimulationResult
    balance: GameBalance
    player_user_id: UUID | None = None
    allocation: Allocation | None = None
    seed: int = Field(default=1, ge=0, le=2**31 - 1)


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
    rng = _random(body.seed)
    content = _practice_content(content, body, pool_ids, rng)
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
    allocation = (body.allocation or player_source.save.allocation).model_copy(
        deep=True,
    )
    economy = player_source.save.economy.model_copy(deep=True)
    stats = equipped_stats(allocation, body.balance, economy)
    run = run.model_copy(
        update={
            **stats,
            "hp": stats["maxHp"],
            "mp": stats["maxMp"],
            "moves": 0,
            "kills": 0,
            "quizCursor": 0,
            "readIds": [],
            "phase": "battle",
        },
    )
    state = GameState(
        save=GameSave(run=run, allocation=allocation, economy=economy, content=content),
    )
    context = await combat_context(body.user_id, state, balance=body.balance)
    return _begin_practice(state, context, region, body.seed, rng)


def _practice_content(
    content: dict,
    body: PracticeRequest,
    pool_ids: set[str],
    rng: Random,
) -> dict:
    """Keep saved enemy sets while carrying only this region's readable quizzes."""
    region = body.level - 1
    old_quizzes = content.get("quizzes", [])
    retained = [
        index
        for index, quiz in enumerate(old_quizzes)
        if str(quiz.get("quiz_id", "")).replace("-", "").lower() in pool_ids
    ]
    remap = {old: new for new, old in enumerate(retained)}
    stored_enemies = (content.get("regionEnemies") or {}).get(str(region), [])
    content = {
        "knowledge": [],
        "quizzes": [old_quizzes[index] for index in retained],
    }
    if stored_enemies:
        roster = []
        for enemy in stored_enemies:
            indexes = [
                remap[index]
                for index in enemy.get("quizIndexes", [enemy.get("quizIndex")])
                if index in remap
            ]
            if indexes:
                roster.append({
                    **enemy,
                    "quizIndex": indexes[0],
                    "quizIndexes": indexes,
                })
    else:
        groups = assign_enemy_quizzes(
            list(range(len(content["quizzes"]))),
            body.balance,
            region,
            rng=rng,
        )
        roster = [
            {
                "id": enemy_identity(body.resource_id.hex, region, i),
                "name": f"領域 {body.level}の敵 {i + 1}",
                "quizIndex": group[0],
                "quizIndexes": group,
            }
            for i, group in enumerate(groups)
        ]
    content["regionEnemies"] = {str(region): roster}
    return content


async def prepare_simulated_practice(body: SimulatedPracticeRequest) -> PracticeSession:
    """Keep trial enemy stats exactly; create no user state or real quizzes."""
    trial = body.simulation
    region = trial.achievement - 1
    quiz_count = max((enemy.quiz_count for enemy in trial.enemies), default=0)
    quizzes = [
        {
            "quiz_id": str(uuid4()),
            "quiz_type": "sent2term",
            "prompt": {"subject": "模擬回答", "answer_kind": "term"},
            "statement": "正解か不正解を選んでください。",
            "options": {"yes": "正解", "no": "不正解"},
            "correct": ["yes"],
            "no_correct_option": False,
            "created": "2026-01-01T00:00:00Z",
        }
        for _ in range(quiz_count)
    ]
    player_source = (
        await read_state(body.player_user_id)
        if body.player_user_id is not None
        else None
    )
    economy = (
        player_source.save.economy.model_copy(deep=True) if player_source else Economy()
    )
    allocation = body.allocation
    if allocation is None:
        allocation = (
            player_source.save.allocation if player_source is not None else Allocation()
        )
    allocation = allocation.model_copy(deep=True)
    stats = equipped_stats(allocation, body.balance, economy)
    state = GameState(
        save=GameSave(
            allocation=allocation,
            economy=economy,
            content={"knowledge": [], "quizzes": quizzes},
            run=Run(
                resourceId="simulation",
                name=f"Power {trial.power} · 領域 {trial.achievement}",
                **stats,
                hp=stats["maxHp"],
                mp=stats["maxMp"],
                moves=0,
                kills=0,
                enemyHp=0,
                enemyMaxHp=1,
                quizCursor=0,
                readIds=[],
                phase="battle",
            ),
        ),
    )
    context = CombatContext(
        balance=body.balance,
        enemies=[
            EnemyStats(
                id=enemy_identity("simulation", region, enemy.index - 1),
                name=f"領域 {trial.achievement}の敵 {enemy.index}",
                quizIndex=0,
                quizIndexes=list(range(enemy.quiz_count)),
                hp=enemy.hp,
                attack=enemy.attack,
                relations=enemy.relations,
                region=region,
            )
            for enemy in trial.enemies
            if enemy.quiz_count > 0
        ],
    )
    return _begin_practice(state, context, region, body.seed, _random(body.seed))


def _begin_practice(
    state: GameState,
    context: CombatContext,
    region: int,
    seed: int,
    rng: Random,
) -> PracticeSession:
    """Share encounter draws and turn initialization for both trial sources."""
    chosen = select_encounter(
        context.enemies,
        context.balance,
        region,
        pick=rng.choice,
        draw=rng.randrange,
    )
    if not chosen:
        raise HTTPException(409, "有効なクイズがありません。母集団を更新してください。")
    context.enemies = chosen
    state.save.battle = BattleMarker(
        id=f"practice-{seed}",
        region=region,
        enemies=[enemy.id for enemy in chosen],
        quizIndices={enemy.id: rng.choice(enemy.quizIndexes) for enemy in chosen},
    )
    now = int(time() * 1000)
    stats = equipped_stats(state.save.allocation, context.balance, state.save.economy)
    state.save.run.answerDeadline = now + stats["answerSeconds"] * 1000
    return PracticeSession(state=state, context=context, seed=seed, server_now=now)


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
    stats = equipped_stats(
        state.save.allocation,
        session.context.balance,
        state.save.economy,
    )
    rng = _random(session.seed, marker.turn)
    incoming_damage = {}
    for enemy in marker.enemies:
        if not results.get(enemy):
            low, _, high = damage_range(
                enemies[enemy].attack,
                stats["defense"],
                session.context.balance,
            )
            incoming_damage[enemy] = rng.randint(low, high)
    damage = sum(incoming_damage.values())
    return {
        "state": apply_turn_result(state, turn, results, damage, stats),
        "results": results,
        "damage": damage,
        "incoming_damage": incoming_damage,
    }


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
