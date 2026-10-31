"""importで一度だけ配布し、指定リソースのダンジョンを開放する鍵."""

from asyncio import sleep

from fastapi import HTTPException
from neo4j.exceptions import TransientError
from neomodel import adb

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.notification.domain import NewNotification, NotificationKind
from tanbun.feature.notification.repo import create_notification
from tanbun.feature.notification.usecase import dispatch_saved_notifications

from .inventory import DungeonKey, Product, consume_use
from .item_categories import ItemCategory
from .state import GameSave, GameState, StateUpdate, compare_and_save, read_state
from .unlocks import MAX_UNLOCKED_DUNGEONS, DungeonUnlock

MAX_GRANT_ATTEMPTS = 3


def opened_resources(save: GameSave) -> set[str]:
    """入場済み・開放済みのいずれも重複開放しない."""
    values = [*save.visitedDungeons, *save.maps, *save.dungeons, *save.clears]
    values.extend(item.resourceId for item in save.unlockedDungeons)
    if save.run:
        values.append(save.run.resourceId)
    return {value.replace("-", "").lower() for value in values}


def key_products(save: GameSave) -> list[Product]:
    """通常カタログと異なり、ユーザーの所持品ごとに対象が決まる."""
    opened = opened_resources(save)
    return [
        Product(
            id=identity,
            name=f"{key.name}のダンジョン開放キー",
            kind="dungeon_unlock",
            category=ItemCategory.UNLOCK_KEY,
            resource_id=key.resource_id,
            price=None,
            amount=0,
            icon="key",
            enabled=key.resource_id not in opened,
        )
        for identity, key in save.economy.dungeon_keys.items()
        if save.economy.inventory.get(identity, 0) > 0
    ]


async def owned_resource_name(
    user_id: UUIDy,
    resource_id: UUIDy,
    *,
    require_content: bool = True,
) -> str:
    """使う直前にも所有権と中身を確認する。削除・譲渡後には使えない."""
    rows, _ = await adb.cypher_query(
        """MATCH (r:Resource {uid:$resource})-[:PARENT*0..]->()
        -[:OWNED]->(:User {uid:$user})
        WHERE NOT $require_content OR EXISTS { MATCH (:Sentence {resource_uid:r.uid}) }
        RETURN DISTINCT coalesce(r.title, 'ダンジョン')""",
        {
            "resource": to_uuid(resource_id).hex,
            "user": to_uuid(user_id).hex,
            "require_content": require_content,
        },
    )
    if not rows:
        raise HTTPException(404, "対象のリソースが見つからないか、利用できません。")
    return rows[0][0][:1000]


async def grant_dungeon_key(user_id: UUIDy, resource_id: UUIDy) -> GameState:
    """同じリソースの再import・同時importで鍵を増やさない."""
    resource = to_uuid(resource_id).hex
    name = await owned_resource_name(user_id, resource, require_content=False)
    identity = f"@dungeon:{resource}"
    for attempt in range(MAX_GRANT_ATTEMPTS):
        previous = await read_state(user_id)
        if identity in previous.save.economy.dungeon_keys:
            return previous
        updated = previous.model_copy(deep=True)
        updated.save.economy.dungeon_keys[identity] = DungeonKey(
            resource_id=resource,
            name=name,
        )
        updated.save.economy.inventory[identity] = int(
            resource not in opened_resources(previous.save),
        )
        updated.revision = previous.revision + 1
        owns_transaction = adb._active_transaction is None  # noqa: SLF001
        try:
            rows, notifications = await save_key_grant(
                user_id,
                previous,
                updated,
                identity,
            )
        except TransientError as cause:
            # Only our own atomic() has completed rollback. An outer import/HTTP
            # transaction must fail as a whole rather than reuse its failed state.
            if (
                cause.code != "Neo.TransientError.Transaction.DeadlockDetected"
                or not owns_transaction
            ):
                raise
            if attempt + 1 < MAX_GRANT_ATTEMPTS:
                await sleep(0.05 * (attempt + 1))
            continue
        if rows:
            if notifications:
                await dispatch_saved_notifications(user_id, notifications)
            return updated
    raise HTTPException(409, "所持品が更新されました。importを再試行してください。")


async def save_key_grant(user_id, previous, updated, identity):
    """キーのCASと通知保存を同じトランザクションで確定・破棄する."""
    notifications = []
    async with atomic():
        rows = await compare_and_save(
            user_id,
            StateUpdate(revision=previous.revision, save=updated.save),
            updated,
        )
        if rows and updated.save.economy.inventory[identity]:
            name = updated.save.economy.dungeon_keys[identity].name
            notifications.append(
                await create_notification(
                    user_id,
                    NewNotification(
                        kind=NotificationKind.DUNGEON_KEY_GRANTED,
                        title="ダンジョン開放キーを入手しました",
                        description=f"「{name[:400]}」の開放キーを1個入手しました。所持品から使うとダンジョンを開放できます。",
                        href="/game/item",
                    ),
                ),
            )
    return rows, notifications


async def use_dungeon_key(user_id: UUIDy, save: GameSave, identity: str) -> None:
    """開放と一回消費を呼び出し元のCASでまとめて確定する."""
    key = save.economy.dungeon_keys.get(identity)
    if not key or save.economy.inventory.get(identity, 0) < 1:
        raise HTTPException(422, "所持しているダンジョン開放キーを選んでください。")
    if key.resource_id in opened_resources(save):
        raise HTTPException(409, "このダンジョンはすでに開放されています。")
    if len(save.unlockedDungeons) >= MAX_UNLOCKED_DUNGEONS:
        raise HTTPException(422, "開放済みダンジョンの上限に達しました。")
    if key.source == "shared":
        from .shared_dungeons import public_resource_name  # noqa: PLC0415

        name = await public_resource_name(user_id, key.resource_id)
    else:
        name = await owned_resource_name(user_id, key.resource_id)
    save.unlockedDungeons.append(
        DungeonUnlock(
            sourceId=key.resource_id,
            resourceId=key.resource_id,
            name=name,
            achievement=0,
            source="shared" if key.source == "shared" else "item",
        ),
    )
    consume_use(save.economy, identity, 1)


async def enroll_opened_key(user_id: UUIDy, save: GameSave, identity: str) -> None:
    """開放状態のCASと同じtransaction内で、共有ダンジョンの参加を確定する."""
    key = save.economy.dungeon_keys.get(identity)
    if not key or key.source != "shared":
        return
    rows, _ = await adb.cypher_query(
        """MATCH (u:User {uid:$user}), (r:Resource {uid:$resource})
        WHERE EXISTS {
            MATCH (r)-[:PARENT*0..]->()-[:OWNED]->(owner:User)
            WHERE owner.uid <> $user AND coalesce(owner.is_active,true)
        }
        MERGE (u)-[:PLAYS_DUNGEON]->(r) RETURN r.uid""",
        {"user": to_uuid(user_id).hex, "resource": key.resource_id},
    )
    if not rows:
        raise HTTPException(404, "対象のダンジョンが見つかりません。")
