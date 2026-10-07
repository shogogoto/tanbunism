"""復習XPの増加で越えたレベルを、同じトランザクションで通知に記録する."""

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.notification.domain import (
    NewNotification,
    Notification,
    NotificationKind,
)
from tanbun.feature.notification.repo import create_notification

from .domain import level_from_xp


async def save_level_up_notifications(
    user_id: UUIDy,
    resource_id: UUIDy,
    resource_name: str,
    *,
    user_xp: int,
    resource_xp: int,
    awarded_xp: int,
    coefficient: int,
) -> list[Notification]:
    """加点前後を同じ係数で比較。重複加点・設定変更だけでは通知しない."""
    notifications = []
    if awarded_xp <= 0:
        return notifications
    for previous_xp, kind, title, href in [
        (
            user_xp,
            NotificationKind.USER_LEVEL_UP,
            "レベルアップ!",
            f"/user/{to_uuid(user_id)}",
        ),
        (
            resource_xp,
            NotificationKind.RESOURCE_LEVEL_UP,
            "リソースがレベルアップ!",
            f"/resource/{to_uuid(resource_id)}",
        ),
    ]:
        previous = level_from_xp(previous_xp, coefficient)
        current = level_from_xp(previous_xp + awarded_xp, coefficient)
        if current <= previous:
            continue
        name = "あなた" if kind == NotificationKind.USER_LEVEL_UP else resource_name
        notifications.append(
            await create_notification(
                user_id,
                NewNotification(
                    kind=kind,
                    title=title,
                    description=f"{name[:400]}: Lv. {previous} → Lv. {current}",
                    href=href,
                ),
            ),
        )
    return notifications
