"""管理者向けメンテナンスAPI."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

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
    OrphanedTanbun,
    ResetUserPasswordRequest,
    ResourceDeletionImpact,
    UpdateUserStatusRequest,
)
from .repo import (
    delete_broken_quizzes,
    delete_orphaned_tanbuns,
    delete_user_account,
    delete_user_resource,
    get_resource_deletion_impact,
    list_broken_quizzes,
    list_orphaned_tanbuns,
    list_user_resources,
    list_users,
    update_user_password_hash,
    update_user_status,
)

router = APIRouter(prefix="/admin", tags=["admin"])


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
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[OrphanedTanbun]:
    """配置を失った現行単文を一覧する."""
    return await list_orphaned_tanbuns(limit=limit)


@router.post("/orphaned-tanbuns/delete")
async def remove_orphaned_tanbuns(
    body: DeleteOrphanedTanbunsRequest,
    _admin: AdminUser,
) -> DeleteOrphanedTanbunsResult:
    """選択された孤立単文を削除または退役させる."""
    return await delete_orphaned_tanbuns(body.sentence_ids)


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


def admin_router() -> APIRouter:
    """管理者向けrouterを返す."""
    return router
