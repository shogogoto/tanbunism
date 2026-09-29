"""ユーザー通知API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from tanbun.config.env import Settings
from tanbun.feature.user.router_util import ActiveUser

from .domain import (
    MarkAllNotificationsReadResult,
    Notification,
    NotificationFeed,
    PushConfiguration,
    PushSubscriptionDraft,
    PushSubscriptionEndpoint,
)
from .repo import (
    delete_push_subscription,
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
    save_push_subscription,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/push/configuration")
async def get_push_configuration(_user: ActiveUser) -> PushConfiguration:
    """Web Pushの公開鍵と利用可否を返す."""
    settings = Settings()
    enabled = bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)
    return PushConfiguration(
        enabled=enabled,
        public_key=settings.VAPID_PUBLIC_KEY if enabled else None,
    )


@router.put("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe_to_push(
    draft: PushSubscriptionDraft,
    user: ActiveUser,
) -> Response:
    """現在の端末をWeb Pushへ登録する."""
    await save_push_subscription(user.uid, draft)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_from_push(
    draft: PushSubscriptionEndpoint,
    user: ActiveUser,
) -> Response:
    """現在の端末のWeb Push登録を解除する."""
    await delete_push_subscription(user.uid, draft.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("")
async def get_notifications(
    user: ActiveUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> NotificationFeed:
    """ログインユーザーの通知を新しい順に取得する."""
    return await list_notifications(user.uid, limit=limit)


@router.post("/{notification_id}/read")
async def read_notification(
    notification_id: UUID,
    user: ActiveUser,
) -> Notification:
    """通知を既読にする."""
    notification = await mark_notification_read(notification_id, user.uid)
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="通知が見つかりません",
        )
    return notification


@router.post("/read-all")
async def read_all_notifications(
    user: ActiveUser,
) -> MarkAllNotificationsReadResult:
    """すべての通知を既読にする."""
    return MarkAllNotificationsReadResult(
        updated_count=await mark_all_notifications_read(user.uid),
    )


def notification_router() -> APIRouter:
    """通知routerを返す."""
    return router
