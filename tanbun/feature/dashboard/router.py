"""個人ダッシュボードAPI."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.user.router_util import ActiveUser

from .domain import PersonalTanbunItem, TanbunExposureResult
from .repo import list_personal_tanbuns, record_tanbun_exposure

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/tanbuns")
async def get_personal_tanbuns(
    user: ActiveUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[PersonalTanbunItem]:
    """所有する単文の個人TLを取得."""
    return await list_personal_tanbuns(
        user.uid,
        datetime.now(TZ).date(),
        limit=limit,
    )


@router.post("/tanbuns/{sentence_id}/exposures")
async def mark_tanbun_seen(
    sentence_id: UUID,
    user: ActiveUser,
) -> TanbunExposureResult:
    """単文を今日見たことを1回だけ記録."""
    result = await record_tanbun_exposure(
        user.uid,
        sentence_id,
        datetime.now(TZ).date(),
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="単文が見つかりません",
        )
    return result


def dashboard_router() -> APIRouter:
    """個人ダッシュボードrouterを返す."""
    return router
