"""ユーザー通知APIのテスト."""

import json
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from starlette import status

from tanbun.conftest import mark_async_test
from tanbun.feature.domain.types import UUIDy
from tanbun.feature.user.testing import aauth_header, aregister

from .domain import (
    NewNotification,
    NotificationFeed,
    NotificationKind,
    PushSubscriptionDraft,
)
from .repo import (
    create_notification,
    list_push_subscriptions,
    save_push_subscription,
)
from .usecase import notify_user


@mark_async_test()
async def test_notifications_are_shared_and_marked_read(ac: AsyncClient) -> None:
    """DB通知を取得し、端末に依存せず既読状態を更新できる."""
    user = await aregister("notification@example.com")
    other = await aregister("other-notification@example.com")
    notification = await create_notification(
        user.uid,
        NewNotification(
            kind=NotificationKind.QUIZ_PREPARATION_COMPLETE,
            title="クイズの準備完了",
            description="3問追加しました。",
            href="/dashboard?view=study-plans",
        ),
    )
    await create_notification(
        other.uid,
        NewNotification(
            kind=NotificationKind.QUIZ_PREPARATION_COMPLETE,
            title="他人の通知",
        ),
    )
    headers = await aauth_header(user.email)

    response = await ac.get("/notifications", headers=headers)
    feed = NotificationFeed.model_validate(response.json())
    assert response.status_code == status.HTTP_200_OK
    assert feed.unread_count == 1
    assert [item.uid for item in feed.notifications] == [notification.uid]

    response = await ac.post(
        f"/notifications/{notification.uid}/read",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["read_at"] is not None

    response = await ac.get("/notifications", headers=headers)
    assert NotificationFeed.model_validate(response.json()).unread_count == 0


@mark_async_test()
async def test_mark_all_notifications_read(ac: AsyncClient) -> None:
    """未読通知をまとめて既読にする."""
    user = await aregister("notification-all@example.com")
    draft = NewNotification(
        kind=NotificationKind.QUIZ_PREPARATION_COMPLETE,
        title="クイズの準備完了",
    )
    await create_notification(user.uid, draft)
    await create_notification(user.uid, draft)
    headers = await aauth_header(user.email)

    response = await ac.post("/notifications/read-all", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"updated_count": 2}
    feed = NotificationFeed.model_validate(
        (await ac.get("/notifications", headers=headers)).json(),
    )
    assert feed.unread_count == 0


@mark_async_test()
async def test_register_and_remove_push_subscription(ac: AsyncClient) -> None:
    """ログインユーザーの端末購読を保存・解除する."""
    user = await aregister("push-subscription@example.com")
    headers = await aauth_header(user.email)
    subscription = {
        "endpoint": "https://push.example.test/subscription/1",
        "expirationTime": None,
        "keys": {
            "p256dh": "browser-public-key",
            "auth": "browser-auth-secret",
        },
    }

    response = await ac.put(
        "/notifications/push/subscriptions",
        json=subscription,
        headers=headers,
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    saved = await list_push_subscriptions(user.uid)
    assert len(saved) == 1
    assert saved[0].model_dump(mode="json", by_alias=True) == subscription

    response = await ac.request(
        "DELETE",
        "/notifications/push/subscriptions",
        json={"endpoint": subscription["endpoint"]},
        headers=headers,
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert await list_push_subscriptions(user.uid) == []


@mark_async_test()
async def test_reject_insecure_push_subscription(ac: AsyncClient) -> None:
    """外部へのPush送信先としてHTTP endpointを保存しない."""
    user = await aregister("insecure-push-subscription@example.com")
    headers = await aauth_header(user.email)

    response = await ac.put(
        "/notifications/push/subscriptions",
        json={
            "endpoint": "http://push.example.test/subscription/1",
            "expirationTime": None,
            "keys": {"p256dh": "public-key", "auth": "auth-secret"},
        },
        headers=headers,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert await list_push_subscriptions(user.uid) == []


@mark_async_test()
async def test_notify_user_sends_web_push_to_registered_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB通知の作成時に購読済み端末へ同じ内容をPushする."""
    user = await aregister("push-delivery@example.com")
    await _save_subscription(user.uid)
    send = AsyncMock()
    monkeypatch.setattr(
        "tanbun.feature.notification.usecase.webpush_async",
        send,
    )
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "application-server-key")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "private-key")

    notification = await notify_user(
        user.uid,
        NewNotification(
            kind=NotificationKind.QUIZ_PREPARATION_COMPLETE,
            title="クイズの準備完了",
            description="2問追加しました。",
            href="/dashboard?view=study-plans",
        ),
    )

    send.assert_awaited_once()
    payload = json.loads(send.await_args.kwargs["data"])
    assert payload["notification_id"] == str(notification.uid)
    assert payload["title"] == notification.title
    assert payload["unread_count"] == 1


async def _save_subscription(user_id: UUIDy) -> None:
    """配信テスト用の端末購読を保存する."""
    await save_push_subscription(
        user_id,
        PushSubscriptionDraft(
            endpoint="https://push.example.test/subscription/delivery",
            keys={
                "p256dh": "browser-public-key",
                "auth": "browser-auth-secret",
            },
        ),
    )
