"""復習設定の本人向けCRUD."""

from fastapi import APIRouter, Response

from tanbun.feature.user.router_util import ActiveUser

from .settings import (
    ReviewSettings,
    ReviewSettingsInput,
    list_settings,
    reset_settings,
    save_settings,
)

router = APIRouter(prefix="/review/settings", tags=["review"])


@router.get("")
async def get_review_settings(user: ActiveUser) -> list[ReviewSettings]:
    """標準・自作設定を一覧."""
    return await list_settings(user.uid)


@router.post("")
async def create_review_settings(
    data: ReviewSettingsInput,
    user: ActiveUser,
) -> ReviewSettings:
    """自作設定を追加."""
    return await save_settings(user.uid, data)


@router.put("/{profile_id}")
async def update_review_settings(
    profile_id: str,
    data: ReviewSettingsInput,
    user: ActiveUser,
) -> ReviewSettings:
    """標準または自作設定を更新."""
    return await save_settings(user.uid, data, profile_id)


@router.delete("/{profile_id}", status_code=204)
async def delete_review_settings(profile_id: str, user: ActiveUser) -> Response:
    """自作設定と、その推薦スナップショットを削除."""
    await reset_settings(user.uid, profile_id, delete=True)
    return Response(status_code=204)


@router.post("/{profile_id}/rebuild", status_code=204)
async def rebuild_review_set(profile_id: str, user: ActiveUser) -> Response:
    """最新設定で今日のセットを再作成可能にする. 復習記録は消さない."""
    await reset_settings(user.uid, profile_id)
    return Response(status_code=204)
