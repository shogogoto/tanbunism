"""ユーザー通知のNeo4jアクセス."""

from uuid import uuid4

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid

from .domain import (
    NewNotification,
    Notification,
    NotificationFeed,
    PushSubscriptionDraft,
)

MAX_STORED_NOTIFICATIONS = 100


async def create_notification(
    user_id: UUIDy,
    draft: NewNotification,
) -> Notification:
    """ユーザーへ通知を保存し、古い通知を上限件数まで掃除する."""
    query = """
        MATCH (user:User {uid: $user_id})
        CREATE (notification:Notification {
            uid: $notification_id,
            kind: $kind,
            title: $title,
            description: $description,
            href: $href,
            created: datetime(),
            read_at: null
        })-[:OWNED]->(user)
        RETURN notification.uid, notification.kind, notification.title,
            notification.description, notification.href,
            notification.created, notification.read_at
    """
    rows, _ = await adb.cypher_query(
        query,
        params={
            "user_id": to_uuid(user_id).hex,
            "notification_id": uuid4().hex,
            "kind": draft.kind.value,
            "title": draft.title,
            "description": draft.description,
            "href": draft.href,
        },
    )
    await _prune_notifications(user_id)
    return _to_notification(rows[0])


async def list_notifications(
    user_id: UUIDy,
    *,
    limit: int,
) -> NotificationFeed:
    """新しい順の通知と全通知の未読数を取得する."""
    query = """
        MATCH (user:User {uid: $user_id})
        OPTIONAL MATCH (notification:Notification)-[:OWNED]->(user)
        WITH user,
            count(CASE
                WHEN notification.uid IS NOT NULL
                    AND notification.read_at IS NULL
                THEN 1
            END) AS unread_count
        CALL (user) {
            MATCH (item:Notification)-[:OWNED]->(user)
            RETURN item
            ORDER BY item.created DESC, item.uid DESC
            LIMIT $limit
        }
        RETURN item.uid, item.kind, item.title, item.description, item.href,
            item.created, item.read_at, unread_count
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"user_id": to_uuid(user_id).hex, "limit": limit},
    )
    if not rows:
        return NotificationFeed(notifications=[], unread_count=0)
    return NotificationFeed(
        notifications=[_to_notification(row[:7]) for row in rows],
        unread_count=rows[0][7],
    )


async def mark_notification_read(
    notification_id: UUIDy,
    user_id: UUIDy,
) -> Notification | None:
    """所有する通知を既読にする."""
    query = """
        MATCH (notification:Notification {uid: $notification_id})
            -[:OWNED]->(:User {uid: $user_id})
        SET notification.read_at = coalesce(notification.read_at, datetime())
        RETURN notification.uid, notification.kind, notification.title,
            notification.description, notification.href,
            notification.created, notification.read_at
    """
    rows, _ = await adb.cypher_query(
        query,
        params={
            "notification_id": to_uuid(notification_id).hex,
            "user_id": to_uuid(user_id).hex,
        },
    )
    return _to_notification(rows[0]) if rows else None


async def mark_all_notifications_read(user_id: UUIDy) -> int:
    """所有する未読通知をすべて既読にする."""
    query = """
        MATCH (notification:Notification)-[:OWNED]->(
            :User {uid: $user_id}
        )
        WHERE notification.read_at IS NULL
        WITH collect(notification) AS notifications
        FOREACH (notification IN notifications |
            SET notification.read_at = datetime()
        )
        RETURN size(notifications)
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"user_id": to_uuid(user_id).hex},
    )
    return rows[0][0] if rows else 0


async def _prune_notifications(user_id: UUIDy) -> None:
    """保存上限を超えた古い通知を削除する."""
    query = """
        MATCH (notification:Notification)-[:OWNED]->(
            :User {uid: $user_id}
        )
        WITH notification
        ORDER BY notification.created DESC, notification.uid DESC
        SKIP $keep
        DETACH DELETE notification
    """
    await adb.cypher_query(
        query,
        params={
            "user_id": to_uuid(user_id).hex,
            "keep": MAX_STORED_NOTIFICATIONS,
        },
    )


def _to_notification(row: list) -> Notification:
    return Notification(
        uid=row[0],
        kind=row[1],
        title=row[2],
        description=row[3],
        href=row[4],
        created=row[5],
        read_at=row[6],
    )


async def save_push_subscription(
    user_id: UUIDy,
    draft: PushSubscriptionDraft,
) -> None:
    """端末のPush購読を現在のユーザーへupsertする."""
    query = """
        MATCH (user:User {uid: $user_id})
        MERGE (subscription:PushSubscription {endpoint: $endpoint})
        ON CREATE SET subscription.uid = $subscription_id,
            subscription.created = datetime()
        SET subscription.p256dh = $p256dh,
            subscription.auth = $auth,
            subscription.expiration_time = $expiration_time,
            subscription.updated = datetime()
        WITH user, subscription
        OPTIONAL MATCH (subscription)-[old_owner:OWNED]->(:User)
        DELETE old_owner
        WITH DISTINCT user, subscription
        MERGE (subscription)-[:OWNED]->(user)
    """
    await adb.cypher_query(
        query,
        params={
            "user_id": to_uuid(user_id).hex,
            "subscription_id": uuid4().hex,
            "endpoint": draft.endpoint,
            "p256dh": draft.keys.p256dh,
            "auth": draft.keys.auth,
            "expiration_time": draft.expiration_time,
        },
    )


async def list_push_subscriptions(user_id: UUIDy) -> list[PushSubscriptionDraft]:
    """ユーザーが有効化した全端末のPush購読を返す."""
    query = """
        MATCH (subscription:PushSubscription)-[:OWNED]->(
            :User {uid: $user_id}
        )
        RETURN subscription.endpoint, subscription.p256dh,
            subscription.auth, subscription.expiration_time
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"user_id": to_uuid(user_id).hex},
    )
    return [
        PushSubscriptionDraft(
            endpoint=row[0],
            keys={"p256dh": row[1], "auth": row[2]},
            expiration_time=row[3],
        )
        for row in rows
    ]


async def delete_push_subscription(user_id: UUIDy, endpoint: str) -> bool:
    """ユーザーの端末購読を削除する."""
    query = """
        MATCH (subscription:PushSubscription {endpoint: $endpoint})
            -[:OWNED]->(:User {uid: $user_id})
        DETACH DELETE subscription
        RETURN true
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"user_id": to_uuid(user_id).hex, "endpoint": endpoint},
    )
    return bool(rows)
