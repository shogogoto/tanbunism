"""管理者向けメンテナンスAPI."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tanbun.feature.entry.resource.import_settings import (
    ResourceImportSettings,
    get_resource_import_settings,
    update_resource_import_settings,
)
from tanbun.feature.game.access import AdventureAccess, reset_adventure_access
from tanbun.feature.game.admin import rebuild_user_enemy_pools
from tanbun.feature.game.balance import (
    GameBalance,
    get_game_balance,
    update_game_balance,
)
from tanbun.feature.game.settings import (
    BattleSettings,
    get_battle_settings,
    update_battle_settings,
)
from tanbun.feature.gamification.power import (
    PowerWeights,
    get_power_weights,
    update_power_weights,
)
from tanbun.feature.gamification.settings import (
    GamificationSettings,
    get_gamification_settings,
    update_gamification_settings,
)
from tanbun.feature.quiz.learning.study_plan.preparation_settings import (
    QuizPreparationSettings,
    get_quiz_preparation_settings,
    update_quiz_preparation_settings,
)
from tanbun.feature.user.manager import get_user_manager
from tanbun.feature.user.router_util import AdminUser

from .domain import (
    AdminBrokenQuiz,
    AdminResourceItem,
    AdminUserItem,
    DeleteAdminResourceRequest,
    DeleteAdminResourceResult,
    DeleteBrokenQuizzesRequest,
    DeleteBrokenQuizzesResult,
    DeleteOrphanedTanbunsRequest,
    DeleteOrphanedTanbunsResult,
    DeleteUserRequest,
    DeleteUserResult,
    GrantAdminRequest,
    OrphanedTanbun,
    ResetUserPasswordRequest,
    ResourceDeletionImpact,
    TanbunIntegrityKind,
    UpdateUserStatusRequest,
)
from .repo import (
    delete_broken_quizzes,
    delete_orphaned_tanbuns,
    delete_user_account,
    delete_user_resource,
    get_resource_deletion_impact,
    grant_user_admin,
    list_broken_quizzes,
    list_orphaned_tanbuns,
    list_user_resources,
    list_users,
    update_user_password_hash,
    update_user_status,
)
from .user_transfer import (
    UserTransferPreview,
    UserTransferRequest,
    preview_user_transfer,
    transfer_user_data,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/settings/game-balance")
async def read_game_balance(_admin: AdminUser) -> GameBalance:
    """現在のゲームバランス."""
    return await get_game_balance()


@router.put("/settings/game-balance")
async def save_game_balance(body: GameBalance, _admin: AdminUser) -> GameBalance:
    """既存の敵にも反映する補正値を保存する."""
    return await update_game_balance(body)


@router.get("/settings/battle")
async def read_battle_settings(_admin: AdminUser) -> BattleSettings:
    """戦闘の基本秒数・種類別の重みを取得する."""
    return await get_battle_settings()


@router.put("/settings/battle")
async def save_battle_settings(
    body: BattleSettings,
    _admin: AdminUser,
) -> BattleSettings:
    """回答履歴・XPや出題中の期限は変更しない."""
    return await update_battle_settings(body)


@router.post("/users/{user_id}/adventure-reset")
async def reset_user_adventure(user_id: UUID, _admin: AdminUser) -> AdventureAccess:
    """指定ユーザーの冒険待ち時間だけ解除する。復習・攻略実績は変更しない."""
    return await reset_adventure_access(user_id)


@router.post("/users/{user_id}/game/enemies/rebuild")
async def rebuild_user_game_enemies(
    user_id: UUID,
    _admin: AdminUser,
) -> dict[str, int]:
    """保存済みの領域別クイズ母集団から敵セットを再構成する."""
    return await rebuild_user_enemy_pools(user_id)


@router.get("/settings/resource-power")
async def get_resource_power_settings(_admin: AdminUser) -> PowerWeights:
    """Powerの適用中の重みを取得する."""
    return await get_power_weights()


@router.put("/settings/resource-power")
async def update_resource_power_settings(
    body: PowerWeights,
    _admin: AdminUser,
) -> PowerWeights:
    """既存のリソースにも適用する重みを保存する。Lv・XPは変更しない."""
    return await update_power_weights(body)


@router.get("/settings/gamification")
async def get_level_settings(_admin: AdminUser) -> GamificationSettings:
    """ユーザー・リソース共通のレベル設定を取得する."""
    return await get_gamification_settings()


@router.put("/settings/gamification")
async def update_level_settings(
    body: GamificationSettings,
    _admin: AdminUser,
) -> GamificationSettings:
    """XPを保持したままレベル係数を更新する."""
    return await update_gamification_settings(body)


@router.get("/settings/resource-import")
async def get_resource_import_limits(
    _admin: AdminUser,
) -> ResourceImportSettings:
    """Resource importの同時実行制限を取得する."""
    return await get_resource_import_settings()


@router.put("/settings/resource-import")
async def update_resource_import_limits(
    body: ResourceImportSettings,
    _admin: AdminUser,
) -> ResourceImportSettings:
    """Resource importの同時実行制限を更新する."""
    return await update_resource_import_settings(body)


@router.get("/settings/quiz-preparation")
async def get_quiz_preparation_limits(
    _admin: AdminUser,
) -> QuizPreparationSettings:
    """一括クイズ準備のサーバー制限を取得する."""
    return await get_quiz_preparation_settings()


@router.put("/settings/quiz-preparation")
async def update_quiz_preparation_limits(
    body: QuizPreparationSettings,
    _admin: AdminUser,
) -> QuizPreparationSettings:
    """一括クイズ準備のサーバー制限を更新する."""
    return await update_quiz_preparation_settings(body)


@router.get("/orphaned-tanbuns")
async def get_orphaned_tanbuns(
    _admin: AdminUser,
    kind: TanbunIntegrityKind = TanbunIntegrityKind.ORPHANED,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[OrphanedTanbun]:
    """孤立またはResource内の配置を失った現行単文を一覧する."""
    return await list_orphaned_tanbuns(kind=kind, limit=limit)


@router.post("/orphaned-tanbuns/delete")
async def remove_orphaned_tanbuns(
    body: DeleteOrphanedTanbunsRequest,
    _admin: AdminUser,
) -> DeleteOrphanedTanbunsResult:
    """選択された孤立単文を削除または退役させる."""
    return await delete_orphaned_tanbuns(body.sentence_ids, body.kind)


@router.get("/broken-quizzes")
async def get_broken_quizzes(
    _admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[AdminBrokenQuiz]:
    """参照切れQuizを一覧する."""
    return await list_broken_quizzes(limit=limit)


@router.post("/broken-quizzes/delete")
async def remove_broken_quizzes(
    body: DeleteBrokenQuizzesRequest,
    _admin: AdminUser,
) -> DeleteBrokenQuizzesResult:
    """参照切れQuizと回答履歴を削除する."""
    return await delete_broken_quizzes(body.quiz_ids)


@router.get("/users")
async def get_users(_admin: AdminUser) -> list[AdminUserItem]:
    """ユーザーと利用状態を一覧する."""
    return await list_users()


@router.patch("/users/{user_id}/status")
async def change_user_status(
    user_id: UUID,
    body: UpdateUserStatusRequest,
    admin: AdminUser,
) -> AdminUserItem:
    """通常ユーザーの利用を停止または再開する."""
    if user_id == admin.uid and not body.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="自分自身を停止することはできません",
        )
    result = await update_user_status(user_id, is_active=body.is_active)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ユーザーが見つかりません",
        )
    if result.is_superuser and not body.is_active:
        await update_user_status(user_id, is_active=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="superuserを停止することはできません",
        )
    return result


@router.put("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: UUID,
    body: ResetUserPasswordRequest,
    admin: AdminUser,
) -> None:
    """通常ユーザーへ管理者が新しいパスワードを設定する."""
    if user_id == admin.uid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="自分自身のパスワードはアカウント設定から変更してください",
        )
    target = next(
        (user for user in await list_users() if user.uid == user_id),
        None,
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ユーザーが見つかりません",
        )
    if target.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="superuserのパスワードは変更できません",
        )
    manager = get_user_manager()
    hashed_password = manager.password_helper.hash(body.password)
    if not await update_user_password_hash(
        user_id,
        hashed_password=hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ユーザーが見つかりません",
        )


@router.post("/users/{user_id}/delete")
async def remove_user(
    user_id: UUID,
    body: DeleteUserRequest,
    admin: AdminUser,
) -> DeleteUserResult:
    """確認済みの通常ユーザーと所有データを削除する."""
    if user_id == admin.uid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="自分自身を削除することはできません",
        )
    target = next(
        (user for user in await list_users() if user.uid == user_id),
        None,
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ユーザーが見つかりません",
        )
    if target.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="superuserを削除することはできません",
        )
    if body.confirmation != target.email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="メールアドレスが一致しません",
        )
    result = await delete_user_account(user_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ユーザーが見つかりません",
        )
    return result


@router.get("/users/{user_id}/resources")
async def get_user_resources(
    user_id: UUID,
    _admin: AdminUser,
) -> list[AdminResourceItem]:
    """ユーザーが所有するResourceを一覧する."""
    resources = await list_user_resources(user_id)
    if resources is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ユーザーが見つかりません",
        )
    return resources


@router.get("/users/{user_id}/transfer-preview")
async def inspect_user_transfer(
    user_id: UUID,
    target_id: UUID,
    _admin: AdminUser,
) -> UserTransferPreview:
    """元ユーザーを保持するデータ移行の事前確認."""
    return await preview_user_transfer(user_id, target_id)


@router.post("/users/{user_id}/transfer")
async def migrate_user_data(
    user_id: UUID,
    body: UserTransferRequest,
    admin: AdminUser,
) -> UserTransferPreview:
    """空の移行先へ一括移行。認証・権限は変えない."""
    return await transfer_user_data(user_id, body, admin.uid)


@router.get("/resources/{resource_id}/deletion-impact")
async def inspect_resource_deletion(
    resource_id: UUID,
    _admin: AdminUser,
) -> ResourceDeletionImpact:
    """Resource削除の影響を確認する."""
    impact = await get_resource_deletion_impact(resource_id)
    if impact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resourceが見つかりません",
        )
    return impact


@router.post("/resources/{resource_id}/delete")
async def remove_user_resource(
    resource_id: UUID,
    body: DeleteAdminResourceRequest,
    _admin: AdminUser,
) -> DeleteAdminResourceResult:
    """確認済みの他ユーザー所有Resourceを安全に削除する."""
    impact = await get_resource_deletion_impact(resource_id)
    if impact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resourceが見つかりません",
        )
    if body.confirmation != impact.resource_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resource名が一致しません",
        )
    result = await delete_user_resource(resource_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resourceが見つかりません",
        )
    return result


@router.post("/users/{user_id}/admin")
async def grant_admin(
    user_id: UUID,
    body: GrantAdminRequest,
    _admin: AdminUser,
) -> AdminUserItem:
    """Grant admin privileges only to an active, confirmed account."""
    users = await list_users()
    target = next((user for user in users if user.uid == user_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ユーザーが見つかりません")
    if body.confirmation != target.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "メールアドレスが一致しません")
    if not target.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "停止中のユーザーは先に再開してください",
        )
    if not await grant_user_admin(user_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "ユーザーの状態が変わりました。再確認してください",
        )
    return target.model_copy(update={"is_superuser": True})


def admin_router() -> APIRouter:
    """管理者向けrouterを返す."""
    return router
