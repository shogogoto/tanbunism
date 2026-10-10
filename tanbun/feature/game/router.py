"""認証済みユーザー本人の冒険権API."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, Field

from tanbun.feature.quiz.learning.study_plan.repo import adventure_quiz_progress
from tanbun.feature.user.router_util import ActiveUser

from .access import AdventureAccess, consume_adventure_access, get_adventure_access
from .balance import Allocation, GameBalance, get_game_balance
from .combat import (
    CombatContext,
    EncounterRequest,
    TurnRequest,
    abandon_combat,
    combat_context,
    next_turn,
    set_allocation,
    settle_turn,
    start_combat,
)
from .knowledge import KnowledgeValidation, valid_knowledge
from .population import (
    MAX_LEGACY_POOL_SIZE,
    MAX_REGION_LEVEL,
    get_or_prepare_region_pool,
)
from .preparation import preparation_status, schedule_dungeon_quizzes
from .roster import assign_enemy_quizzes, enemy_identity
from .state import GameState, StateUpdate, read_state, recover_state, write_state

router = APIRouter(prefix="/game", tags=["game"])


class RegionQuizPoolSeed(BaseModel):
    """既存スナップショットの母集団をグラフへ移行する入力."""

    quiz_ids: list[str] = Field(min_length=1, max_length=MAX_LEGACY_POOL_SIZE)


@router.get("/balance")
async def read_balance(user: ActiveUser, response: Response) -> GameBalance:
    """クライアントの表示用設定."""
    response.headers["Cache-Control"] = "no-store"
    return await get_game_balance()


@router.get("/battle/context")
async def read_combat_context(user: ActiveUser, response: Response) -> CombatContext:
    """能力値は既存の敵でも最新値を返す."""
    response.headers["Cache-Control"] = "no-store"
    return await combat_context(user.uid)


@router.post("/battle/start")
async def begin_combat(user: ActiveUser, body: EncounterRequest) -> GameState:
    """遭遇開始マーカーを保存."""
    return await start_combat(user.uid, body)


@router.post("/battle/turn")
async def resolve_combat(user: ActiveUser, body: TurnRequest) -> dict:
    """確定回答とゲーム結果の一括保存."""
    return await settle_turn(user.uid, body)


@router.post("/battle/next")
async def begin_next_turn(user: ActiveUser, body: TurnRequest) -> GameState:
    """結果確認後に持ち時間を開始."""
    return await next_turn(user.uid, body)


@router.post("/battle/abandon")
async def retreat_unfinished_combat(user: ActiveUser) -> GameState:
    """再読み込みで未完了戦闘を撤退扱いにする."""
    return await abandon_combat(user.uid)


@router.put("/allocation")
async def edit_allocation(user: ActiveUser, body: Allocation) -> GameState:
    """育成ポイントの振り直し."""
    return await set_allocation(user.uid, body)


@router.post("/knowledge/validate")
async def validate_game_knowledge(
    body: KnowledgeValidation,
    user: ActiveUser,
    response: Response,
) -> list[str]:
    """読み取り専用。閲覧記録と同じ条件で候補の有効性を確認."""
    response.headers["Cache-Control"] = "no-store"
    return await valid_knowledge(user.uid, body)


@router.get("/state")
async def get_game_state(
    user: ActiveUser,
    response: Response,
    background_tasks: BackgroundTasks,
) -> GameState:
    """認証中のアカウントの冒険を読み出す."""
    response.headers["Cache-Control"] = "no-store"
    state = await read_state(user.uid)
    await schedule_dungeon_quizzes(user.uid, state, background_tasks)
    return state


@router.put("/state")
async def put_game_state(
    user: ActiveUser,
    update: StateUpdate,
    background_tasks: BackgroundTasks,
) -> GameState:
    """更新番号を確認して冒険を保存する."""
    state = await write_state(user.uid, update)
    await schedule_dungeon_quizzes(user.uid, state, background_tasks)
    return state


@router.get("/dungeons/{resource_id}/preparation")
async def get_dungeon_preparation(
    resource_id: UUID,
    user: ActiveUser,
    response: Response,
    background_tasks: BackgroundTasks,
) -> dict[str, int]:
    """準備済み領域を返し、開拓済みの追加準備があれば再開する."""
    response.headers["Cache-Control"] = "no-store"
    state = await read_state(user.uid)
    await schedule_dungeon_quizzes(
        user.uid,
        state,
        background_tasks,
        [resource_id.hex],
    )
    _plan_id, prepared_regions = await adventure_quiz_progress(user.uid, resource_id)
    return preparation_status(state, prepared_regions, resource_id.hex)


@router.get("/dungeons/{resource_id}/regions/{level}/quiz-pool")
async def get_dungeon_region_quiz_pool(
    resource_id: UUID,
    level: int,
    user: ActiveUser,
    response: Response,
) -> dict[str, object]:
    """領域レベルごとに固定した累積クイズ母集団を返す."""
    if not 1 <= level <= MAX_REGION_LEVEL:
        raise HTTPException(422, "領域レベルは1から20の範囲で指定してください。")
    response.headers["Cache-Control"] = "no-store"
    return await _region_pool_response(user.uid, resource_id, level)


@router.post("/dungeons/{resource_id}/regions/{level}/quiz-pool")
async def migrate_dungeon_region_quiz_pool(
    resource_id: UUID,
    level: int,
    seed: RegionQuizPoolSeed,
    user: ActiveUser,
    response: Response,
) -> dict[str, object]:
    """既存スナップショットの固定母集団を検証してグラフへ移行する."""
    if not 1 <= level <= MAX_REGION_LEVEL:
        raise HTTPException(422, "領域レベルは1から20の範囲で指定してください。")
    response.headers["Cache-Control"] = "no-store"
    return await _region_pool_response(user.uid, resource_id, level, seed.quiz_ids)


async def _region_pool_response(
    user_id: UUID,
    resource_id: UUID,
    level: int,
    legacy_ids: list[str] | None = None,
) -> dict[str, object]:
    pool = await get_or_prepare_region_pool(user_id, resource_id, level, legacy_ids)
    balance = await get_game_balance()
    quiz_ids = pool["quiz_ids"]
    groups = assign_enemy_quizzes(list(range(len(quiz_ids))), balance, level - 1)
    return {
        **pool,
        "enemies": [
            {
                "id": enemy_identity(resource_id.hex, level - 1, index),
                "name": f"領域 {level}の敵 {index + 1}",
                "quiz_ids": [quiz_ids[position] for position in group],
            }
            for index, group in enumerate(groups)
        ]
        if pool["ready"]
        else [],
        "enemy_types": balance.enemy_type_count(level - 1),
        "min_quizzes_per_enemy": balance.min_quizzes_per_enemy,
        "max_quizzes_per_enemy": balance.max_quizzes_per_enemy,
    }


@router.post("/state/recover")
async def recover_game_state(user: ActiveUser, response: Response) -> GameState:
    """時計枠の歩数補充を原子的に保存する。累積もHP回復もしない."""
    response.headers["Cache-Control"] = "no-store"
    return await recover_state(user.uid)


@router.get("/adventure-access")
async def read_adventure_access(
    user: ActiveUser,
    response: Response,
) -> AdventureAccess:
    """端末の時計・ローカル保存ではなくサーバーの使用済み状態を返す."""
    response.headers["Cache-Control"] = "no-store"
    return await get_adventure_access(user.uid)


@router.post("/adventure-access/consume")
async def use_adventure_access(user: ActiveUser) -> AdventureAccess:
    """入場または休憩からの再開時に冒険権を消費する."""
    return await consume_adventure_access(user.uid)
