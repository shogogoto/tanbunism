"""Resourceを分散させた重み付き抽選と、日替わりセットの保存."""

from asyncio import sleep
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from math import log

from fastapi import HTTPException
from neo4j.exceptions import TransientError
from neomodel import adb

from tanbun.feature.domain.datetime import TZ

RETENTION_DAYS = 7
DEADLOCK_ATTEMPTS = 5


async def _daily_query(query: str, params: dict) -> tuple:
    """冪等な日次保存だけ、ロールバックされたdeadlockを上限付きで再試行."""
    for attempt in range(DEADLOCK_ATTEMPTS):
        try:
            return await adb.cypher_query(query, params=params)
        except TransientError as error:
            if (
                error.code != "Neo.TransientError.Transaction.DeadlockDetected"
                or attempt == DEADLOCK_ATTEMPTS - 1
            ):
                raise
            await sleep(0.05 * (attempt + 1))
    msg = "Daily query retry limit exceeded"
    raise RuntimeError(msg)


def review_day(day: date | None = None) -> date:
    """公開APIの指定日は日本時間の今日を含む7日間のみ."""
    today = datetime.now(TZ).date()
    result = day or today
    if not today - timedelta(days=RETENTION_DAYS - 1) <= result <= today:
        raise HTTPException(
            status_code=422,
            detail="今日を含む7日以内を選択してください",
        )
    return result


async def prune_daily(user_id: str) -> None:
    """最新の保存日から7日分を保持. 遅延リクエストで新しい日を消さない."""
    await _daily_query(
        """
        MATCH (user:User {uid:$uid})-[:RECOMMENDATIONS|REVIEW_DAY]->(s)
        WITH user, max(s.day) AS latest
        MATCH (user)-[:RECOMMENDATIONS|REVIEW_DAY]->(old)
        WHERE old.day < latest - duration({days:6})
        DETACH DELETE old
        """,
        params={"uid": user_id},
    )


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
    """日付ごとに保持し、同日の先行セットを優先する."""
    rows, _ = await _daily_query(
        """
        MATCH (user:User {uid: $user_id})
        SET user.review_settings_revision=coalesce(user.review_settings_revision,0)+1
        WITH user
        MERGE (user)-[:RECOMMENDATIONS]->(s:DailyRecommendation {
            scope: $scope, day: date($day)})
        ON CREATE SET s.ids = $ids
        WITH s
        FOREACH (_ IN CASE WHEN size(s.ids) = 0
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
    await prune_daily(user_id)
    return rows[0][0] if rows else []


async def append_daily(
    user_id: str,
    scope: str,
    day: date,
    ids: list[str],
) -> list[str]:
    """追加復習では既存の順序を残し、重複せず最大500件まで追加する."""
    rows, _ = await _daily_query(
        """
        MATCH (user:User {uid:$user_id})
        SET user.review_settings_revision=coalesce(user.review_settings_revision, 0)+1
        WITH user
        MERGE (user)-[:RECOMMENDATIONS]->(s:DailyRecommendation {
            scope:$scope, day:date($day)})
        ON CREATE SET s.ids=[]
        WITH s
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
    await prune_daily(user_id)
    return rows[0][0] if rows else []
