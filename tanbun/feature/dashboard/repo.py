"""個人ダッシュボードのNeo4jアクセス."""

from datetime import date

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.repo.cypher import q_call_term_names
from tanbun.feature.tanbun.repo.clause import OrderBy
from tanbun.feature.tanbun.repo.cypher import q_location, q_stats

from .domain import PersonalTanbunItem, TanbunExposureResult


async def count_tanbun_exposures(user_id: UUIDy, seen_on: date) -> int:
    """指定日にユーザーが閲覧記録を付けた単文数を返す."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (exposure:TanbunExposure {
            user_id: $user_id,
            seen_on: date($seen_on)
        })
        RETURN count(exposure)
        """,
        params={
            "user_id": to_uuid(user_id).hex,
            "seen_on": seen_on.isoformat(),
        },
    )
    return rows[0][0] if rows else 0


async def list_personal_tanbuns(
    user_id: UUIDy,
    seen_on: date,
    *,
    limit: int = 30,
) -> list[PersonalTanbunItem]:
    """新着と未遭遇の古い単文を混ぜ、古い知識も再登場させる."""
    recent_limit = max(1, (limit * 2 + 2) // 3)
    recent = await _fetch_personal_tanbuns(
        user_id,
        seen_on,
        limit=recent_limit,
        rediscovery=False,
    )
    rediscovery = await _fetch_personal_tanbuns(
        user_id,
        seen_on,
        limit=limit,
        rediscovery=True,
    )
    combined: list[PersonalTanbunItem] = []
    included = set()
    recent_index = 0
    rediscovery_index = 0
    while len(combined) < limit:
        added = False
        for _ in range(2):
            while recent_index < len(recent):
                item = recent[recent_index]
                recent_index += 1
                if item.uid in included:
                    continue
                combined.append(item)
                included.add(item.uid)
                added = True
                break
        while rediscovery_index < len(rediscovery) and len(combined) < limit:
            item = rediscovery[rediscovery_index]
            rediscovery_index += 1
            if item.uid in included:
                continue
            combined.append(item)
            included.add(item.uid)
            added = True
            break
        if not added:
            break
    return combined


async def _fetch_personal_tanbuns(
    user_id: UUIDy,
    seen_on: date,
    *,
    limit: int,
    rediscovery: bool,
) -> list[PersonalTanbunItem]:
    """新着順または、遭遇が少なく古い順で単文を取得."""
    location_query = q_location("sentence")
    term_names_query = q_call_term_names("sentence")
    stats_query = q_stats("sentence", OrderBy())
    query = (
        """
        MATCH (resource:Resource)-[:PARENT*0..]->(owner_entry)
            -[:OWNED]->(:User {uid: $user_id})
        MATCH (sentence:Sentence {resource_uid: resource.uid})
        OPTIONAL MATCH (exposure:TanbunExposure {
            user_id: $user_id,
            sentence_id: sentence.uid
        })
        WITH resource, sentence,
            count(exposure) AS exposure_count,
            count(CASE WHEN exposure.seen_on = date($seen_on) THEN 1 END) > 0
                AS seen_today
        ORDER BY
            seen_today ASC,
            CASE WHEN $rediscovery THEN exposure_count END ASC,
            CASE WHEN $rediscovery THEN resource.updated END ASC,
            CASE WHEN NOT $rediscovery THEN resource.updated END DESC,
            sentence.uid ASC
        LIMIT $candidate_limit
        """
        + location_query
        + """
        WITH resource, sentence, exposure_count, seen_today, location
        WHERE location IS NOT NULL
        """
        + term_names_query
        + stats_query
        + """
        ORDER BY
            seen_today ASC,
            CASE WHEN $rediscovery THEN exposure_count END ASC,
            stats.score DESC,
            CASE WHEN $rediscovery THEN resource.updated END ASC,
            CASE WHEN NOT $rediscovery THEN resource.updated END DESC,
            sentence.uid ASC
        LIMIT $limit
        RETURN sentence.uid, sentence.val,
            coalesce([name IN names | name.val], []) AS term_names,
            resource.uid, resource.title, resource.updated, stats.score,
            exposure_count, seen_today
        """
    )
    rows, _ = await adb.cypher_query(
        query,
        params={
            "user_id": to_uuid(user_id).hex,
            "seen_on": seen_on.isoformat(),
            "limit": limit,
            "candidate_limit": max(limit * 3, 30),
            "rediscovery": rediscovery,
        },
    )
    return [
        PersonalTanbunItem(
            uid=row[0],
            sentence=row[1],
            term_names=row[2],
            resource_uid=row[3],
            resource_name=row[4],
            updated_at=row[5],
            score=row[6],
            exposure_count=row[7],
            seen_today=row[8],
        )
        for row in rows
    ]


async def record_tanbun_exposure(
    user_id: UUIDy,
    sentence_id: UUIDy,
    seen_on: date,
) -> TanbunExposureResult | None:
    """所有する単文へ、同じ日は重複しない閲覧記録を残す."""
    uid = to_uuid(user_id).hex
    sentence_uid = to_uuid(sentence_id).hex
    location_query = q_location("sentence")
    query = (
        """
        MATCH (resource:Resource)-[:PARENT*0..]->(owner_entry)
            -[:OWNED]->(:User {uid: $user_id})
        MATCH (sentence:Sentence {
            uid: $sentence_id,
            resource_uid: resource.uid
        })
        """
        + location_query
        + """
        WITH sentence, location
        WHERE location IS NOT NULL
        OPTIONAL MATCH (previous:TanbunExposure {key: $key})
        WITH sentence, previous IS NULL AS recorded
        MERGE (exposure:TanbunExposure {key: $key})
        ON CREATE SET exposure.user_id = $user_id,
            exposure.sentence_id = $sentence_id,
            exposure.seen_on = date($seen_on),
            exposure.created = datetime()
        WITH sentence, recorded
        MATCH (all_exposures:TanbunExposure {
            user_id: $user_id,
            sentence_id: $sentence_id
        })
        RETURN recorded, count(all_exposures)
        """
    )
    rows, _ = await adb.cypher_query(
        query,
        params={
            "user_id": uid,
            "sentence_id": sentence_uid,
            "seen_on": seen_on.isoformat(),
            "key": f"{uid}:{sentence_uid}:{seen_on.isoformat()}",
        },
    )
    if not rows:
        return None
    return TanbunExposureResult(
        sentence_id=sentence_id,
        seen_on=seen_on,
        exposure_count=rows[0][1],
        recorded=rows[0][0],
    )
