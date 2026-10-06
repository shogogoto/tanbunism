"""Resourceを分散させた重み付き抽選と、日替わりセットの保存."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from math import log

from neomodel import adb


@dataclass(frozen=True)
class Candidate:
    """軽量な推薦候補."""

    uid: str
    resource_id: str
    weight: float = 1


def select_daily(candidates: list[Candidate], seed: str, limit: int) -> list[str]:
    """日付固定の重み付き抽選をResourceごとに行い、交互に取り出す."""

    def rank(candidate: Candidate) -> float:
        digest = sha256(f"{seed}:{candidate.uid}".encode()).digest()
        uniform = (int.from_bytes(digest[:8]) + 1) / (2**64 + 1)
        return -log(uniform) / max(candidate.weight, 0.001)

    groups: dict[str, list[Candidate]] = defaultdict(list)
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda c: (rank(c), c.uid, c.resource_id)):
        if candidate.uid in seen:
            continue
        seen.add(candidate.uid)
        groups[candidate.resource_id].append(candidate)
    result: list[str] = []
    while groups and len(result) < limit:
        for resource_id in sorted(groups, key=lambda key: rank(groups[key][0])):
            result.append(groups[resource_id].pop(0).uid)
            if not groups[resource_id]:
                del groups[resource_id]
            if len(result) == limit:
                break
    return result


async def load_daily(user_id: str, scope: str, day: date) -> list[str] | None:
    """同日のセットがあれば返す. 空セットも保持する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (:User {uid: $user_id})-[:RECOMMENDATIONS]->(s:DailyRecommendation)
        WHERE s.scope = $scope AND s.day = date($day)
        RETURN s.ids
        """,
        params={"user_id": user_id, "scope": scope, "day": day.isoformat()},
    )
    return rows[0][0] if rows else None


async def save_daily(
    user_id: str,
    scope: str,
    day: date,
    ids: list[str],
) -> list[str]:
    """ユーザー・用途ごとに1セットだけ保持し、同日の先行セットを優先する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_id})
        MERGE (user)-[:RECOMMENDATIONS]->(s:DailyRecommendation {scope: $scope})
        ON CREATE SET s.day = date($day), s.ids = $ids
        WITH s
        FOREACH (_ IN CASE WHEN s.day < date($day)
            OR (s.day = date($day) AND size(s.ids) = 0)
            THEN [1] ELSE [] END |
            SET s.day = date($day), s.ids = $ids
        )
        RETURN s.ids
        """,
        params={
            "user_id": user_id,
            "scope": scope,
            "day": day.isoformat(),
            "ids": ids,
        },
    )
    return rows[0][0] if rows else []


async def append_daily(
    user_id: str,
    scope: str,
    day: date,
    ids: list[str],
) -> list[str]:
    """追加復習では既存の順序を残し、重複せず最大500件まで追加する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid:$user_id})
        SET user.review_settings_revision=coalesce(user.review_settings_revision, 0)+1
        WITH user
        MERGE (user)-[:RECOMMENDATIONS]->(s:DailyRecommendation {scope:$scope})
        ON CREATE SET s.day=date($day), s.ids=[]
        WITH s
        FOREACH (_ IN CASE WHEN s.day < date($day) THEN [1] ELSE [] END |
            SET s.day=date($day), s.ids=[])
        WITH s WHERE s.day=date($day)
        SET s.ids=(s.ids + [id IN $ids WHERE NOT id IN s.ids])[0..500]
        RETURN s.ids
        """,
        params={
            "user_id": user_id,
            "scope": scope,
            "day": day.isoformat(),
            "ids": list(dict.fromkeys(ids)),
        },
    )
    return rows[0][0] if rows else []
