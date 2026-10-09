"""個人ダッシュボードのNeo4jアクセス."""

from datetime import date, datetime
from math import log1p

from neomodel import adb

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.gamification.resource import record_exposure_xp
from tanbun.feature.notification.usecase import dispatch_saved_notifications
from tanbun.feature.recommendation.daily import (
    Candidate,
    append_daily,
    load_daily,
    save_daily,
    select_daily,
)
from tanbun.feature.recommendation.settings import (
    ReviewPriority,
    settings_scope,
    today_settings,
)
from tanbun.feature.repo.cypher import q_call_term_names
from tanbun.feature.repo.pagerank import cached_rank
from tanbun.feature.tanbun.repo.clause import OrderBy
from tanbun.feature.tanbun.repo.cypher import q_location, q_stats
from tanbun.feature.tanbun.repo.reviewable import q_reviewable_sentences

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
    profile_id: str = "default",
    more: bool = False,
    historical: bool = False,
) -> list[PersonalTanbunItem]:
    """未遭遇・久しぶり・スコアを重みに、同日の推薦セットを返す."""
    uid = to_uuid(user_id).hex
    scope = settings_scope(profile_id, "tanbuns")
    ids = await load_daily(uid, scope, seen_on)
    if historical:
        return await _fetch_personal_tanbuns(user_id, seen_on, ids=(ids or [])[:limit])
    settings = await today_settings(user_id, profile_id, seen_on)
    if more or not ids:
        rows, _ = await adb.cypher_query(
            f"""
            MATCH (resource:Resource)-[:PARENT*0..]->()-[:OWNED]->(:User {{uid: $uid}})
            MATCH (sentence:Sentence {{resource_uid: resource.uid}})
            WHERE $resource_ids IS NULL OR resource.uid IN $resource_ids
            WITH DISTINCT resource, sentence
            OPTIONAL MATCH (exposure:TanbunExposure {{
                user_id: $uid, sentence_id: sentence.uid
            }})
            RETURN sentence.uid, resource.uid, count(exposure), max(exposure.seen_on)
                , {
                cached_rank("sentence", "resource")
                if settings.priority == ReviewPriority.PAGERANK
                else "null"
            } AS pagerank_score
            """,
            params={
                "uid": uid,
                "resource_ids": (
                    [r.hex for r in settings.resource_ids]
                    if settings.resource_ids
                    else None
                ),
            },
        )
        weights: dict[str, float] = {}
        pageranks: dict[str, float] = {}
        candidates = []
        for sentence_id, resource_id, count, last_seen, rank in rows:
            if ids and sentence_id in ids:
                continue
            days = (seen_on - last_seen.to_native()).days if last_seen else 30
            weight = (4 if count == 0 else 1 / (1 + count)) * max(
                0.05,
                min(days / 14, 1),
            )
            if settings.priority == ReviewPriority.UNSEEN:
                weight *= 5 if count == 0 else 1
            elif settings.priority == ReviewPriority.WEAK:
                # 単文では正誤がないため、接触が少なく間隔の空いたものを優先する。
                weight *= max(1, days) / (1 + count)
            elif settings.priority == ReviewPriority.SCORE:
                weight = 1
            weights[sentence_id] = weight
            if settings.priority == ReviewPriority.PAGERANK and rank is not None:
                pageranks[sentence_id] = rank
            candidates.append(
                Candidate(
                    sentence_id,
                    resource_id,
                    weight * (1 + log1p(max(0, pageranks.get(sentence_id, 0)))),
                ),
            )
        seed = f"{uid}:{scope}:{seen_on}"
        # 高コストの位置・スコア取得は分散した候補に限定する。
        pool = select_daily(candidates, seed, 150)
        items = await _fetch_personal_tanbuns(user_id, seen_on, ids=pool)
        additions = select_daily(
            [
                Candidate(
                    item.uid.hex,
                    item.resource_uid.hex,
                    weights[item.uid.hex]
                    * (1 + log1p(max(0, pageranks.get(item.uid.hex, item.score)))),
                )
                for item in items
            ],
            seed,
            settings.tanbun_count,
        )
        ids = await (
            append_daily(uid, scope, seen_on, additions)
            if more
            else save_daily(uid, scope, seen_on, additions)
        )
    return await _fetch_personal_tanbuns(user_id, seen_on, ids=ids[:limit])


async def _fetch_personal_tanbuns(
    user_id: UUIDy,
    seen_on: date,
    *,
    ids: list[str],
) -> list[PersonalTanbunItem]:
    """推薦IDの順に、現行で所有する単文と最新の閲覧状況を復元."""
    location_query = q_location("sentence")
    term_names_query = q_call_term_names("sentence")
    stats_query = q_stats("sentence", OrderBy())
    query = (
        """
        UNWIND range(0, size($ids) - 1) AS position
        MATCH (sentence:Sentence {uid: $ids[position]})
        MATCH (resource:Resource {uid: sentence.resource_uid})
            -[:PARENT*0..]->(owner_entry)
            -[:OWNED]->(:User {uid: $user_id})
        WITH DISTINCT resource, sentence, position
        OPTIONAL MATCH (exposure:TanbunExposure {
            user_id: $user_id,
            sentence_id: sentence.uid
        })
        WITH resource, sentence, position,
            count(exposure) AS exposure_count,
            count(CASE WHEN exposure.seen_on = date($today) THEN 1 END) > 0
                AS seen_today,
            count(CASE WHEN exposure.seen_on >= date($seen_on) THEN 1 END) > 0
                AS seen_in_set
        """
        + location_query
        + """
        WITH resource, sentence, position, exposure_count,
            seen_today, seen_in_set, location
        WHERE location IS NOT NULL
        """
        + term_names_query
        + stats_query
        + """
        ORDER BY position
        RETURN sentence.uid, sentence.val,
            coalesce([name IN names | name.val], []) AS term_names,
            resource.uid, resource.title, resource.updated, stats.score,
            exposure_count, seen_today, seen_in_set
        """
    )
    rows, _ = await adb.cypher_query(
        query,
        params={
            "user_id": to_uuid(user_id).hex,
            "seen_on": seen_on.isoformat(),
            "today": datetime.now(TZ).date().isoformat(),
            "ids": ids,
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
            seen_in_set=row[9],
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
    query = (
        q_reviewable_sentences()
        + """
        WITH DISTINCT sentence
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
    async with adb.transaction:
        rows, _ = await adb.cypher_query(
            query,
            params={
                "user_id": uid,
                "sentence_id": sentence_uid,
                "sentence_ids": [sentence_uid],
                "resource_id": None,
                "seen_on": seen_on.isoformat(),
                "key": f"{uid}:{sentence_uid}:{seen_on.isoformat()}",
            },
        )
        if not rows:
            return None
        notifications = await record_exposure_xp(
            user_id,
            sentence_id,
            seen_on.isoformat(),
        )
    await dispatch_saved_notifications(user_id, notifications)
    return TanbunExposureResult(
        sentence_id=sentence_id,
        seen_on=seen_on,
        exposure_count=rows[0][1],
        recorded=rows[0][0],
    )
