"""PageRank管理API。計算自体はリクエスト中に行わない."""

from uuid import UUID

from fastapi import APIRouter, HTTPException

from tanbun.feature.user.router_util import AdminUser

from .domain import PageRankRequest, PageRankSettings
from .repo import PageRankStore

router = APIRouter(prefix="/admin/pagerank", tags=["admin"])


@router.get("/settings")
async def settings(_admin: AdminUser) -> PageRankSettings:
    """独立した負荷制御設定."""
    return await PageRankStore().settings()


@router.put("/settings")
async def save_settings(body: PageRankSettings, _admin: AdminUser) -> PageRankSettings:
    """次のジョブ取得から適用。実行中の処理は中断しない."""
    return await PageRankStore().save_settings(body)


@router.get("/resources")
async def resources(_admin: AdminUser) -> list[dict]:
    """再取り込み不要の対象選択と鮮度確認."""
    return await PageRankStore().resources()


@router.get("/jobs")
async def jobs(_admin: AdminUser) -> list[dict]:
    """全adminで共有するキューの進捗."""
    return await PageRankStore().jobs()


@router.post("/jobs", status_code=202)
async def enqueue(body: PageRankRequest, admin: AdminUser) -> dict:
    """UUID表記を正規化して選択リソースを一括投入."""
    try:
        ids = [UUID(value).hex for value in body.resource_ids]
    except ValueError as error:
        raise HTTPException(422, "リソースIDが不正です。") from error
    try:
        return await PageRankStore().enqueue(ids, UUID(str(admin.uid)).hex)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
