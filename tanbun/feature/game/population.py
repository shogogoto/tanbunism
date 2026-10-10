"""Resource/user scoped quiz populations for dungeon regions."""

from __future__ import annotations

from uuid import UUID, uuid5

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid

REGION_QUIZ_COUNT = 5
MAX_REGION_LEVEL = 20
MAX_LEGACY_POOL_SIZE = 100
DUNGEON_NAMESPACE = UUID("8d25ce4d-aed0-4ac4-9c39-57016342c98b")


def _dungeon_uid(user_id: UUIDy, resource_id: UUIDy) -> str:
    return uuid5(
        DUNGEON_NAMESPACE,
        f"{to_uuid(user_id).hex}:{to_uuid(resource_id).hex}",
    ).hex


def _region_uid(dungeon_uid: str, level: int) -> str:
    return uuid5(DUNGEON_NAMESPACE, f"{dungeon_uid}:{level}").hex


async def _stored_region_pool(region_uid: str) -> list[str] | None:
    rows, _ = await adb.cypher_query(
        """
        MATCH (:DungeonRegion {uid: $region_uid})-[population:POPULATION_QUIZ]->(
            quiz:Quiz
        )
        RETURN quiz.uid
        ORDER BY population.position
        """,
        {"region_uid": region_uid},
    )
    return [row[0] for row in rows] if rows else None


async def _available_quiz_ids(
    user_id: UUIDy,
    resource_id: UUIDy,
    excluded: list[str],
    limit: int,
) -> list[str]:
    rows, _ = await adb.cypher_query(
        """
        MATCH (plan:StudyPlan {auto_resource_uid: $resource_uid})
            -[:OWNED]->(user:User {uid: $user_uid})
        MATCH (user)-[:LEARN]->(quiz:Quiz)-[:QUIZ_TARGET]->(
            sentence:Sentence {resource_uid: $resource_uid}
        )
        WHERE quiz.quiz_type IN plan.quiz_types
          AND NOT quiz.uid IN $excluded
          AND NOT EXISTS { MATCH (quiz)-[:BROKEN_BY]->() }
        WITH DISTINCT quiz
        RETURN quiz.uid
        ORDER BY quiz.created ASC, quiz.uid ASC
        LIMIT $limit
        """,
        {
            "user_uid": to_uuid(user_id).hex,
            "resource_uid": to_uuid(resource_id).hex,
            "excluded": excluded,
            "limit": limit,
        },
    )
    return [row[0] for row in rows]


async def _validate_quiz_ids(
    user_id: UUIDy,
    resource_id: UUIDy,
    quiz_ids: list[str],
) -> bool:
    if not quiz_ids or len(quiz_ids) != len(set(quiz_ids)):
        return False
    rows, _ = await adb.cypher_query(
        """
        MATCH (plan:StudyPlan {auto_resource_uid: $resource_uid})
            -[:OWNED]->(user:User {uid: $user_uid})
        MATCH (user)-[:LEARN]->(quiz:Quiz)-[:QUIZ_TARGET]->(
            sentence:Sentence {resource_uid: $resource_uid}
        )
        WHERE quiz.uid IN $quiz_ids
          AND quiz.quiz_type IN plan.quiz_types
          AND NOT EXISTS { MATCH (quiz)-[:BROKEN_BY]->() }
        RETURN count(DISTINCT quiz)
        """,
        {
            "user_uid": to_uuid(user_id).hex,
            "resource_uid": to_uuid(resource_id).hex,
            "quiz_ids": quiz_ids,
        },
    )
    return bool(rows and rows[0][0] == len(quiz_ids))


async def _save_region_pool(
    user_id: UUIDy,
    resource_id: UUIDy,
    level: int,
    quiz_ids: list[str],
) -> None:
    dungeon_uid = _dungeon_uid(user_id, resource_id)
    region_uid = _region_uid(dungeon_uid, level)
    await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_uid})
        MATCH (resource:Resource {uid: $resource_uid})
        MERGE (dungeon:Dungeon {uid: $dungeon_uid})
        ON CREATE SET dungeon.user_uid = $user_uid,
                      dungeon.resource_uid = $resource_uid
        MERGE (user)-[:HAS_DUNGEON]->(dungeon)
        MERGE (dungeon)-[:BASED_ON]->(resource)
        MERGE (region:DungeonRegion {uid: $region_uid})
        ON CREATE SET region.level = $level
        MERGE (dungeon)-[:HAS_REGION]->(region)
        WITH region
        OPTIONAL MATCH (region)-[old:POPULATION_QUIZ]->()
        DELETE old
        WITH DISTINCT region
        UNWIND range(0, size($quiz_ids) - 1) AS position
        MATCH (quiz:Quiz {uid: $quiz_ids[position]})
        CREATE (region)-[:POPULATION_QUIZ {position: position}]->(quiz)
        RETURN count(quiz)
        """,
        {
            "user_uid": to_uuid(user_id).hex,
            "resource_uid": to_uuid(resource_id).hex,
            "dungeon_uid": dungeon_uid,
            "region_uid": region_uid,
            "level": level,
            "quiz_ids": quiz_ids,
        },
    )


async def get_or_prepare_region_pool(
    user_id: UUIDy,
    resource_id: UUIDy,
    level: int,
    legacy_pool: list[str] | None = None,
) -> dict[str, object]:
    """Return a stable cumulative pool; create its graph nodes once prepared."""
    dungeon_uid = _dungeon_uid(user_id, resource_id)
    collected: list[str] = []
    seed_ids: list[str] = []
    if legacy_pool:
        existing = await _stored_region_pool(_region_uid(dungeon_uid, level))
        if existing is not None:
            legacy_pool = None
        elif len(legacy_pool) > MAX_LEGACY_POOL_SIZE or not await _validate_quiz_ids(
            user_id,
            resource_id,
            legacy_pool,
        ):
            return {
                "ready": False,
                "level": level,
                "required_quizzes": level * REGION_QUIZ_COUNT,
                "available_quizzes": 0,
                "quiz_ids": [],
            }
        else:
            seed_ids = legacy_pool
    for current_level in range(1, level + 1):
        region_uid = _region_uid(dungeon_uid, current_level)
        stored = await _stored_region_pool(region_uid)
        if stored is not None:
            collected = stored
        required = current_level * REGION_QUIZ_COUNT
        needed = max(0, required - len(collected))
        legacy_additions = [
            quiz_id for quiz_id in seed_ids if quiz_id not in collected
        ][:needed]
        collected.extend(legacy_additions)
        needed = max(0, required - len(collected))
        candidates = (
            await _available_quiz_ids(
                user_id,
                resource_id,
                collected,
                needed,
            )
            if needed
            else []
        )
        if len(candidates) < needed:
            return {
                "ready": False,
                "level": level,
                "required_quizzes": level * REGION_QUIZ_COUNT,
                "available_quizzes": len(collected) + len(candidates),
                "quiz_ids": [],
            }
        collected = [*collected, *candidates]
        if stored is None or len(stored) < required:
            await _save_region_pool(user_id, resource_id, current_level, collected)

    if len(collected) < level * REGION_QUIZ_COUNT:
        return {
            "ready": False,
            "level": level,
            "required_quizzes": level * REGION_QUIZ_COUNT,
            "available_quizzes": len(collected),
            "quiz_ids": [],
        }
    return {
        "ready": True,
        "level": level,
        "required_quizzes": level * REGION_QUIZ_COUNT,
        "available_quizzes": len(collected),
        "quiz_ids": collected,
    }
