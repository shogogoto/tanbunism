"""認証済みユーザー本人の冒険権API."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, Field

from tanbun.feature.quiz.learning.study_plan.preparation_state import (
    PreparationState,
    preparation_states,
)
from tanbun.feature.quiz.repo.restore import restore_quiz_sources
from tanbun.feature.user.router_util import ActiveUser

from .access import AdventureAccess, consume_adventure_access, get_adventure_access
from .balance import Allocation, GameBalance, get_game_balance
from .boss import BossRequest, start_boss
from .catalog import GameCatalog, get_game_catalog
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
from .dungeon_keys import key_products
from .economy import (
    EconomyAction,
    catalog_products,
    change_economy,
    grant_review_reward,
    quiz_review_progress,
    review_progress,
)
from .end_actions import (
    EndActionAnswer,
    EndActionRequest,
    answer_end_action,
    start_end_action,
)
from .gambit_slots import GambitCapacity, gambit_capacity
from .gambits import GambitRule, validate_rules
from .knowledge import KnowledgeValidation, valid_knowledge
from .population import (
    MAX_LEGACY_POOL_SIZE,
    MAX_REGION_LEVEL,
    _validate_quiz_ids,
    get_or_prepare_region_pool,
)
from .preferences import BattlePreferences, battle_preferences
from .preparation import (
    dungeon_quiz_progress,
    preparation_status,
    schedule_dungeon_quizzes,
)
from .roster import assign_enemy_quizzes, enemy_identity
from .skills import (
    SkillAnswer,
    SkillRequest,
    resolve_skill,
    specialty_pools,
    start_skill,
)
from .specialties import SpecialtyUpdate, specialty_view, update_specialties
from .state import (
    GameState,
    StateUpdate,
    read_state,
    recover_state,
    refresh_dungeon_unlocks,
    write_state,
)
from .treasure import ChestRequest, open_chest

router = APIRouter(prefix="/game", tags=["game"])


@router.get("/specialties")
async def read_specialties(user: ActiveUser) -> dict:
    """所持専門分野と装備枠・現在の強さを返す."""
    return await specialty_view(user.uid)


@router.post("/specialties")
async def change_specialties(user: ActiveUser, body: SpecialtyUpdate) -> GameState:
    """専門分野の作成・配合・装備を保存する."""
    return await update_specialties(user.uid, body)


@router.post("/battle/gambit/start")
async def begin_end_action(user: ActiveUser, body: EndActionRequest) -> dict:
    """ターン終了時の自動行動を開始する."""
    return await start_end_action(user.uid, body)


@router.post("/battle/gambit/answer")
async def answer_gambit_quiz(user: ActiveUser, body: EndActionAnswer) -> dict:
    """配合元ごとの専用クイズに回答する."""
    return await answer_end_action(user.uid, body)


@router.get("/catalog")
async def read_catalog(user: ActiveUser, response: Response) -> GameCatalog:
    """ガンビットと素材一覧に使う定義。."""
    response.headers["Cache-Control"] = "no-store"
    return await get_game_catalog()


class GambitUpdate(BaseModel):
    """本人の優先順付き自動行動設定。."""

    revision: int = Field(ge=0)
    rules: list[GambitRule] = Field(max_length=10)


@router.get("/gambits/capacity")
async def read_gambit_capacity(user: ActiveUser, response: Response) -> GambitCapacity:
    """本人のレベルで開放された枠数を返す."""
    response.headers["Cache-Control"] = "no-store"
    return await gambit_capacity(user.uid)


@router.put("/gambits")
async def save_gambits(user: ActiveUser, body: GambitUpdate) -> GameState:
    """戦闘外でのみ編集し、別端末の更新を上書きしない。."""
    from tanbun.config.transactions import atomic  # noqa: PLC0415

    from .combat import persist  # noqa: PLC0415

    state = await read_state(user.uid)
    if (
        state.revision != body.revision
        or state.save.battle
        or state.save.battleFeedback
        or (state.save.run and state.save.run.phase == "battle")
    ):
        raise HTTPException(409, "戦闘を終えてから最新の状態で設定してください。")
    await validate_gambit_update(body.rules)
    if len(body.rules) > (await gambit_capacity(user.uid)).slots:
        raise HTTPException(422, "ガンビットの開放済み枠数を超えています。")
    updated = state.model_copy(deep=True)
    updated.save.gambits = body.rules
    async with atomic():
        await persist(user.uid, state, updated)
    return updated


async def validate_gambit_update(rules: list[GambitRule]) -> None:
    """カタログの実在する戦闘行動だけを保存する。."""
    validate_rules(rules, await get_game_catalog())


@router.post("/chests/open")
async def claim_chest(user: ActiveUser, body: ChestRequest) -> GameState:
    """領域終端の宝箱を開封し、報酬と開封状態を一緒に保存する."""
    return await open_chest(user.uid, body)


@router.get("/battle/preferences")
async def read_battle_preferences(user: ActiveUser) -> BattlePreferences:
    """本人の戦闘テンポを取得する."""
    return await battle_preferences(user.uid)


@router.put("/battle/preferences")
async def save_battle_preferences(
    user: ActiveUser,
    body: BattlePreferences,
) -> BattlePreferences:
    """戦闘中も演出速度だけを変更できる."""
    return await battle_preferences(user.uid, body)


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


@router.post("/battle/boss")
async def begin_boss(user: ActiveUser, body: BossRequest) -> GameState:
    """達成度の節目で任意にボスへ挑戦する."""
    return await start_boss(user.uid, body)


@router.post("/battle/skill/start")
async def begin_skill(user: ActiveUser, body: SkillRequest) -> dict:
    """MPを消費して攻略済みリソースのクイズを抽選する."""
    return await start_skill(user.uid, body)


@router.post("/battle/skill/answer")
async def answer_skill(user: ActiveUser, body: SkillAnswer) -> dict:
    """専門分野の回答を学習履歴にも記録する."""
    return await resolve_skill(user.uid, body)


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
    state, _ = await grant_review_reward(user.uid)
    state, _ = await grant_review_reward(user.uid, quiz=True)
    state = await refresh_dungeon_unlocks(user.uid, state)
    await schedule_dungeon_quizzes(user.uid, state, background_tasks)
    return state


@router.get("/shop")
async def get_shop(user: ActiveUser, response: Response) -> dict:
    """ショップの品目と今日の復習報酬の進捗."""
    response.headers["Cache-Control"] = "no-store"
    state = await read_state(user.uid)
    progress = await review_progress(user.uid)
    quiz_progress = await quiz_review_progress(user.uid)
    return {
        "products": [
            p.model_dump()
            for p in [
                *catalog_products(await get_game_catalog(), include_disabled=True),
                *key_products(state.save),
            ]
        ],
        "specialties": [
            {
                "resource_id": p["resource_id"],
                "name": p["name"],
                "level": max(state.save.bossClears[p["resource_id"]]),
                "quiz_count": len(p["quiz_ids"]),
            }
            for p in await specialty_pools(user.uid, state)
        ],
        "review": {
            **progress,
            "claimed": state.save.economy.review_reward_day == progress["day"],
        },
        "quiz_review": {
            **quiz_progress,
            "claimed": state.save.economy.quiz_reward_day == quiz_progress["day"],
        },
    }


@router.post("/shop/buy")
async def buy_product(user: ActiveUser, body: EconomyAction) -> GameState:
    """ゴールドを払い所持品を追加する."""
    return await change_economy(user.uid, body, "buy")


@router.post("/inventory/equip")
async def equip_product(user: ActiveUser, body: EconomyAction) -> GameState:
    """武器を装備・解除する."""
    return await change_economy(user.uid, body, "equip")


@router.post("/inventory/use")
async def use_product(user: ActiveUser, body: EconomyAction) -> GameState:
    """回復薬を一個消費する."""
    return await change_economy(user.uid, body, "use")


@router.put("/state")
async def put_game_state(
    user: ActiveUser,
    update: StateUpdate,
    background_tasks: BackgroundTasks,
) -> GameState:
    """更新番号を確認して冒険を保存する."""
    state = await write_state(user.uid, update)
    state, _ = await grant_review_reward(user.uid)
    state, _ = await grant_review_reward(user.uid, quiz=True)
    await schedule_dungeon_quizzes(user.uid, state, background_tasks)
    return state


@router.get("/dungeons/{resource_id}/preparation")
async def get_dungeon_preparation(
    resource_id: UUID,
    user: ActiveUser,
    response: Response,
    background_tasks: BackgroundTasks,
) -> dict:
    """準備済み領域を返し、開拓済みの追加準備があれば再開する."""
    response.headers["Cache-Control"] = "no-store"
    state = await read_state(user.uid)
    await schedule_dungeon_quizzes(
        user.uid,
        state,
        background_tasks,
        [resource_id.hex],
    )
    plan_id, prepared_regions = await dungeon_quiz_progress(user.uid, resource_id.hex)
    statuses = await preparation_states(user.uid)
    return preparation_status(
        state,
        prepared_regions,
        resource_id.hex,
        statuses.get(plan_id.hex, PreparationState())
        if plan_id
        else PreparationState(),
    )


@router.post("/dungeons/{resource_id}/preparation/retry")
async def retry_dungeon_preparation(
    resource_id: UUID,
    user: ActiveUser,
    response: Response,
    background_tasks: BackgroundTasks,
) -> dict:
    """失敗・候補不足で停止した同じStudyPlanの準備を再試行する."""
    state = await read_state(user.uid)
    await schedule_dungeon_quizzes(
        user.uid,
        state,
        background_tasks,
        [resource_id.hex],
        force=True,
    )
    return await get_dungeon_preparation(resource_id, user, response, background_tasks)


@router.get("/dungeons/{resource_id}/regions/{level}/quiz-pool")
async def get_dungeon_region_quiz_pool(
    resource_id: UUID,
    level: int,
    user: ActiveUser,
    response: Response,
    *,
    include_quizzes: bool = False,
) -> dict[str, object]:
    """領域レベルごとに固定した累積クイズ母集団を返す."""
    if not 1 <= level <= MAX_REGION_LEVEL:
        raise HTTPException(422, "領域レベルは1から20の範囲で指定してください。")
    response.headers["Cache-Control"] = "no-store"
    return await _region_pool_response(
        user.uid,
        resource_id,
        level,
        include_quizzes=include_quizzes,
    )


@router.post("/dungeons/{resource_id}/regions/{level}/quiz-pool")
async def migrate_dungeon_region_quiz_pool(
    resource_id: UUID,
    level: int,
    seed: RegionQuizPoolSeed,
    user: ActiveUser,
    response: Response,
    *,
    include_quizzes: bool = False,
) -> dict[str, object]:
    """既存スナップショットの固定母集団を検証してグラフへ移行する."""
    if not 1 <= level <= MAX_REGION_LEVEL:
        raise HTTPException(422, "領域レベルは1から20の範囲で指定してください。")
    response.headers["Cache-Control"] = "no-store"
    return await _region_pool_response(
        user.uid,
        resource_id,
        level,
        seed.quiz_ids,
        include_quizzes=include_quizzes,
    )


async def _region_pool_response(
    user_id: UUID,
    resource_id: UUID,
    level: int,
    legacy_ids: list[str] | None = None,
    *,
    include_quizzes: bool = False,
) -> dict[str, object]:
    pool = await get_or_prepare_region_pool(user_id, resource_id, level, legacy_ids)
    balance = await get_game_balance()
    quiz_ids = pool["quiz_ids"]
    groups = assign_enemy_quizzes(list(range(len(quiz_ids))), balance, level - 1)
    hydrated = {}
    if include_quizzes and pool["ready"]:
        if not await _validate_quiz_ids(user_id, resource_id, quiz_ids):
            raise HTTPException(
                409,
                "領域の母集団に使用できないクイズがあります。母集団を再構成してください。",
            )
        sources = await restore_quiz_sources(quiz_ids)
        hydrated["quizzes"] = [
            source.to_readable().model_dump(mode="json") for source in sources
        ]
    return {
        **pool,
        **hydrated,
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
        "min_quizzes_per_enemy": balance.enemy_quiz_count(level - 1),
        "max_quizzes_per_enemy": balance.enemy_quiz_count(level - 1),
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
