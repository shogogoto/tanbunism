"""DB通知とWeb Push配信を組み合わせる."""

import asyncio
import json
import logging

from pywebpush import WebPushException, webpush_async

from tanbun.config.env import Settings
from tanbun.feature.domain.types import UUIDy

from .domain import NewNotification, Notification, PushSubscriptionDraft
from .repo import (
    create_notification,
    delete_push_subscription,
    list_notifications,
    list_push_subscriptions,
)

logger = logging.getLogger(__name__)


async def notify_user(
    user_id: UUIDy,
    draft: NewNotification,
) -> Notification:
    """通知をDBへ保存し、購読済みの全端末へWeb Pushする."""
    notification = await create_notification(user_id, draft)
    await dispatch_saved_notifications(user_id, [notification])
    return notification


async def dispatch_saved_notifications(
    user_id: UUIDy,
    notifications: list[Notification],
) -> None:
    """確定済み通知を配信する。配信失敗で完了済みの操作を失敗させない."""
    for notification in notifications:
        try:
            await _dispatch_web_push(user_id, notification)
        except Exception:
            logger.exception(
                "Web Push dispatch failed for notification %s",
                notification.uid,
            )


async def _dispatch_web_push(
    user_id: UUIDy,
    notification: Notification,
) -> None:
    settings = Settings()
    if not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        return
    subscriptions = await list_push_subscriptions(user_id)
    if not subscriptions:
        return
    unread_count = (await list_notifications(user_id, limit=1)).unread_count
    payload = json.dumps(
        {
            "notification_id": str(notification.uid),
            "title": notification.title,
            "body": notification.description or "",
            "url": notification.href or "/",
            "icon": "/icon-192.png",
            "badge": "/icon-192.png",
            "unread_count": unread_count,
        },
        ensure_ascii=False,
    )
    await asyncio.gather(
        *[
            _send_to_subscription(user_id, subscription, payload, settings)
            for subscription in subscriptions
        ],
    )


async def _send_to_subscription(
    user_id: UUIDy,
    subscription: PushSubscriptionDraft,
    payload: str,
    settings: Settings,
) -> None:
    try:
        await webpush_async(
            subscription_info=subscription.model_dump(
                mode="json",
                by_alias=True,
                exclude={"expiration_time"},
            ),
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=60 * 60,
            timeout=5,
        )
    except WebPushException as error:
        if error.status_code in {404, 410}:
            await delete_push_subscription(user_id, subscription.endpoint)
            return
        logger.warning("Web Push delivery failed with status %s", error.status_code)
    except Exception:
        logger.exception("Unexpected Web Push delivery failure")
