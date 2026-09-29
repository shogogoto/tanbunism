"""管理者向けメンテナンスAPI."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tanbun.feature.user.router_util import AdminUser

from .domain import (
    AdminResourceItem,
    AdminUserItem,
    DeleteAdminResourceRequest,
    DeleteAdminResourceResult,
    DeleteOrphanedTanbunsRequest,
    DeleteOrphanedTanbunsResult,
    OrphanedTanbun,
    ResourceDeletionImpact,
    UpdateUserStatusRequest,
)
from .repo import (
    delete_orphaned_tanbuns,
    delete_user_resource,
    get_resource_deletion_impact,
    list_orphaned_tanbuns,
    list_user_resources,
    list_users,
    update_user_status,
)

router = APIRouter(prefix="/admin", tags=["admin"])


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
