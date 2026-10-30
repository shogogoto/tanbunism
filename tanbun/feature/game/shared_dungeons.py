"""公開リソースのダンジョンを、自分の冒険として追加する."""

from uuid import UUID

from fastapi import HTTPException
from neomodel import adb

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import UUIDy, to_uuid

from .dungeon_keys import opened_resources
from .state import GameState, StateUpdate, compare_and_save, read_state
from .unlocks import MAX_UNLOCKED_DUNGEONS, DungeonUnlock


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
    return [
        dict(
            zip(
                ("resource_id", "name", "owner_id", "owner_name", "joined"),
                row,
                strict=True,
            ),
        )
        for row in rows
    ]


async def join_dungeon(user_id: UUID, resource_id: UUID, revision: int) -> GameState:
    """所持・学習・進行を譲渡せず、本人だけの開放と参加をまとめて保存する."""
    async with atomic():
        rows, _ = await adb.cypher_query(
            """MATCH (r:Resource {uid:$resource})
                -[:PARENT*0..]->()-[:OWNED]->(owner:User)
            WHERE owner.uid <> $user AND coalesce(owner.is_active,true)
              AND EXISTS { MATCH (:Sentence {resource_uid:r.uid}) }
            RETURN DISTINCT coalesce(r.title,'ダンジョン')""",
            {"resource": resource_id.hex, "user": user_id.hex},
        )
        if not rows:
            raise HTTPException(404, "対象のダンジョンが見つかりません。")
        previous = await read_state(user_id)
        if previous.revision != revision:
            raise HTTPException(
                409,
                "冒険状態が更新されました。再読み込みしてください。",
            )
        updated = previous.model_copy(deep=True)
        if resource_id.hex not in opened_resources(updated.save):
            if len(updated.save.unlockedDungeons) >= MAX_UNLOCKED_DUNGEONS:
                raise HTTPException(422, "開放済みダンジョンの上限に達しました。")
            updated.save.unlockedDungeons.append(
                DungeonUnlock(
                    sourceId=resource_id.hex,
                    resourceId=resource_id.hex,
                    name=rows[0][0][:1000],
                    achievement=0,
                    source="shared",
                ),
            )
        updated.revision += 1
        if not await compare_and_save(
            user_id,
            StateUpdate(revision=revision, save=updated.save),
            updated,
        ):
            raise HTTPException(
                409,
                "冒険状態が更新されました。再読み込みしてください。",
            )
        await adb.cypher_query(
            """MATCH (u:User {uid:$user}), (r:Resource {uid:$resource})
            MERGE (u)-[:PLAYS_DUNGEON]->(r)""",
            {"user": user_id.hex, "resource": resource_id.hex},
        )
        return updated
