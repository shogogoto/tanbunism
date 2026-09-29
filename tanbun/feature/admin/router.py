"""管理者向けメンテナンスAPI."""

from typing import Annotated

from fastapi import APIRouter, Query

from tanbun.feature.user.router_util import AdminUser

from .domain import (
    DeleteOrphanedTanbunsRequest,
    DeleteOrphanedTanbunsResult,
    OrphanedTanbun,
)
from .repo import delete_orphaned_tanbuns, list_orphaned_tanbuns

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


def admin_router() -> APIRouter:
    """管理者向けrouterを返す."""
    return router
