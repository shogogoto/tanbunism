"""公開リソースのダンジョンを、自分の冒険として追加する."""

from uuid import UUID

from fastapi import HTTPException
from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.notification.usecase import dispatch_saved_notifications

from .dungeon_keys import opened_resources, save_key_grant
from .inventory import DungeonKey
from .state import GameState, read_state


async def dungeon_accessible(user_id: UUIDy, resource_id: UUIDy) -> bool:
    """所有者か、公開ダンジョンを明示的に追加した本人だけが準備できる."""
    rows, _ = await adb.cypher_query(
        """MATCH (r:Resource {uid:$resource})
        WHERE EXISTS { MATCH (r)-[:PARENT*0..]->()-[:OWNED]->(:User {uid:$user}) }
           OR EXISTS { MATCH (:User {uid:$user})-[:PLAYS_DUNGEON]->(r) }
        RETURN r.uid""",
        {"resource": to_uuid(resource_id).hex, "user": to_uuid(user_id).hex},
    )
    return bool(rows)


async def public_dungeons(user_id: UUID, query: str, offset: int) -> list[dict]:
    """既存の公開本棚と同じ所有リソースから、本文なしの候補を返す."""
    rows, _ = await adb.cypher_query(
        """MATCH (r:Resource)-[:PARENT*0..]->()-[:OWNED]->(owner:User)
        WHERE owner.uid <> $user AND coalesce(owner.is_active, true)
          AND EXISTS { MATCH (:Sentence {resource_uid:r.uid}) }
          AND (toLower(coalesce(r.title,'')) CONTAINS toLower($query)
            OR toLower(coalesce(owner.display_name,'')) CONTAINS toLower($query))
        WITH DISTINCT r, owner
        RETURN r.uid, coalesce(r.title,'ダンジョン'), owner.uid,
            coalesce(owner.display_name,'ユーザー'),
            EXISTS { MATCH (:User {uid:$user})-[:PLAYS_DUNGEON]->(r) }
        ORDER BY r.title, r.uid, owner.uid SKIP $offset LIMIT 30""",
        {"user": user_id.hex, "query": query, "offset": offset},
    )
    state = await read_state(user_id)
    opened = opened_resources(state.save)
    return [
        {
            **dict(
                zip(
                    ("resource_id", "name", "owner_id", "owner_name", "joined"),
                    row,
                    strict=True,
                ),
            ),
            "joined": row[4] or row[0] in opened,
            "key_owned": state.save.economy.inventory.get(f"@dungeon:{row[0]}", 0) > 0,
        }
        for row in rows
    ]


async def public_resource_name(user_id: UUIDy, resource_id: UUIDy) -> str:
    """取得時と使用時に、現在も公開本棚の所有リソースであることを確認する."""
    rows, _ = await adb.cypher_query(
        """MATCH (r:Resource {uid:$resource})
                -[:PARENT*0..]->()-[:OWNED]->(owner:User)
            WHERE owner.uid <> $user AND coalesce(owner.is_active,true)
              AND EXISTS { MATCH (:Sentence {resource_uid:r.uid}) }
            RETURN DISTINCT coalesce(r.title,'ダンジョン')""",
        {"resource": to_uuid(resource_id).hex, "user": to_uuid(user_id).hex},
    )
    if not rows:
        raise HTTPException(404, "対象のダンジョンが見つかりません。")
    return rows[0][0][:1000]


async def claim_dungeon_key(
    user_id: UUID,
    resource_id: UUID,
    revision: int,
) -> GameState:
    """書庫では鍵を一個だけ渡し、使用するまでは探索・クイズを開放しない."""
    name = await public_resource_name(user_id, resource_id)
    previous = await read_state(user_id)
    if previous.revision != revision:
        raise HTTPException(409, "冒険状態が更新されました。再読み込みしてください。")
    if (
        previous.save.battle
        or previous.save.battleFeedback
        or (previous.save.run and previous.save.run.phase == "battle")
    ):
        raise HTTPException(409, "戦闘が終わってから開放キーを入手してください。")
    identity = f"@dungeon:{resource_id.hex}"
    if (
        resource_id.hex in opened_resources(previous.save)
        or identity in previous.save.economy.dungeon_keys
    ):
        return previous
    updated = previous.model_copy(deep=True)
    updated.save.economy.dungeon_keys[identity] = DungeonKey(
        resource_id=resource_id.hex,
        name=name,
        source="shared",
    )
    updated.save.economy.inventory[identity] = 1
    updated.revision += 1
    rows, notifications = await save_key_grant(user_id, previous, updated, identity)
    if not rows:
        raise HTTPException(409, "冒険状態が更新されました。再読み込みしてください。")
    await dispatch_saved_notifications(user_id, notifications)
    return updated
