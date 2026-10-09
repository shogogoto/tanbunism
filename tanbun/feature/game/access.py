"""冒険権は時計の30分枠ごとに1回。HP・攻略・XPとは独立して管理する."""

from time import time

from fastapi import HTTPException, status
from neomodel import adb
from pydantic import BaseModel

from tanbun.feature.domain.types import UUIDy, to_uuid

SLOT_SECONDS = 30 * 60


class AdventureAccess(BaseModel):
    """サーバー時刻と次の固定回復時刻 (Unixミリ秒)."""

    available: bool
    server_now: int
    next_available_at: int


def access_status(now: float, consumed_slot: int = -1) -> AdventureAccess:
    """未使用分は繰り越さず、毎時00分・30分で回復する."""
    slot = int(now // SLOT_SECONDS)
    return AdventureAccess(
        available=consumed_slot < slot,
        server_now=int(now * 1000),
        next_available_at=(slot + 1) * SLOT_SECONDS * 1000,
    )


async def get_adventure_access(user_id: UUIDy) -> AdventureAccess:
    """読み取りではユーザーに何も書き込まない."""
    rows, _ = await adb.cypher_query(
        """MATCH (user:User {uid: $uid})
        RETURN coalesce(user.adventure_consumed_slot, -1)""",
        params={"uid": to_uuid(user_id).hex},
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ユーザーが見つかりません。")
    return access_status(time(), rows[0][0])


async def consume_adventure_access(user_id: UUIDy) -> AdventureAccess:
    """Userへの書き込みロック後に判定し、別端末の同時消費も一回に限定する."""
    now = time()
    slot = int(now // SLOT_SECONDS)
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $uid})
        SET user.adventure_access_lock = coalesce(user.adventure_access_lock, 0) + 1
        WITH user
        WHERE coalesce(user.adventure_consumed_slot, -1) < $slot
        SET user.adventure_consumed_slot = $slot
        RETURN user.uid
        """,
        params={"uid": to_uuid(user_id).hex, "slot": slot},
    )
    if not rows:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "この時間帯の冒険権は使用済みです。毎時00分・30分に回復します。",
        )
    return access_status(now, slot)


async def reset_adventure_access(user_id: UUIDy) -> AdventureAccess:
    """adminが待ち時間だけ解除する。繰り返しても冒険権は蓄積しない."""
    rows, _ = await adb.cypher_query(
        """MATCH (user:User {uid: $uid})
        SET user.adventure_consumed_slot = -1
        RETURN user.uid""",
        params={"uid": to_uuid(user_id).hex},
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ユーザーが見つかりません。")
    return access_status(time())
