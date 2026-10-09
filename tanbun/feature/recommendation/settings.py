"""本人の復習設定と、今日適用する設定のスナップショット."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from fastapi import HTTPException
from neomodel import adb
from pydantic import BaseModel, Field

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import UUIDy, to_uuid

from .daily import prune_daily


class ReviewPriority(StrEnum):
    """細かな係数ではなく、推薦の意図を選ぶ."""

    BALANCED = "balanced"
    UNSEEN = "unseen"
    WEAK = "weak"
    SCORE = "score"
    PAGERANK = "pagerank"


class ReviewSettingsInput(BaseModel):
    """Noneなら本人の全リソース. 空の個別選択は許可しない."""

    name: str = Field(min_length=1, max_length=64, pattern=r"\S")
    resource_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=2000)
    tanbun_count: int = Field(default=30, ge=1, le=100)
    quiz_count: int = Field(default=20, ge=1, le=100)
    priority: ReviewPriority = ReviewPriority.BALANCED


class ReviewSettings(ReviewSettingsInput):
    """設定IDはユーザーの所有範囲内で解決する."""

    id: str = "default"


def settings_scope(profile_id: str, kind: str) -> str:
    """設定ごとに推薦セットを分離する."""
    return f"review:{profile_id}:{kind}"


async def list_settings(user_id: UUIDy) -> list[ReviewSettings]:
    """標準設定は保存されていなくても返す."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (:User {uid:$uid})-[:REVIEW_SETTINGS]->(setting:ReviewSettings)
        RETURN setting.config ORDER BY setting.id
        """,
        params={"uid": to_uuid(user_id).hex},
    )
    settings = [ReviewSettings.model_validate_json(row[0]) for row in rows]
    default = next(
        (s for s in settings if s.id == "default"),
        ReviewSettings(name="今日"),
    )
    if default.name == "標準":
        default = default.model_copy(update={"name": "今日"})
    return [default, *(s for s in settings if s.id != "default")]


async def get_settings(user_id: UUIDy, profile_id: str) -> ReviewSettings:
    """他人の設定IDを指定しても取得できない."""
    if profile_id.startswith("plan:"):
        try:
            plan_id = UUID(profile_id.removeprefix("plan:"))
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail="学習計画が見つかりません",
            ) from error
        # Read only the owned plan's recommendation scope. Quiz generation and
        # its domain model do not belong to recommendation settings.
        rows, _ = await adb.cypher_query(
            """
            MATCH (plan:StudyPlan {uid:$plan_id})-[:OWNED]->(:User {uid:$user_id})
            MATCH (plan)-[study:STUDY]->(resource:Resource)
            WITH plan, study, resource ORDER BY study.position ASC
            RETURN plan.name, collect(resource.uid)
            """,
            params={"plan_id": plan_id.hex, "user_id": to_uuid(user_id).hex},
        )
        if not rows:
            raise HTTPException(status_code=404, detail="学習計画が見つかりません")
        # 設定を複製せず、知識の推薦にはPlanの対象Resourceだけ適用する。
        return ReviewSettings(
            id=profile_id,
            name=rows[0][0].strip()[:64] or "学習計画",
            resource_ids=rows[0][1],
        )
    for setting in await list_settings(user_id):
        if setting.id == profile_id:
            return setting
    raise HTTPException(status_code=404, detail="復習設定が見つかりません")


async def save_settings(
    user_id: UUIDy,
    data: ReviewSettingsInput,
    profile_id: str | None = None,
) -> ReviewSettings:
    """対象は所有リソースのみ. 編集しても今日の設定は置換しない."""
    uid = to_uuid(user_id).hex
    if profile_id is not None:
        await get_settings(user_id, profile_id)
    if data.resource_ids is not None:
        rows, _ = await adb.cypher_query(
            """
            MATCH (r:Resource)-[:PARENT*0..]->()-[:OWNED]->(:User {uid:$uid})
            WHERE r.uid IN $ids RETURN collect(DISTINCT r.uid)
            """,
            params={"uid": uid, "ids": [r.hex for r in data.resource_ids]},
        )
        if set(rows[0][0]) != {r.hex for r in data.resource_ids}:
            raise HTTPException(
                status_code=403,
                detail="所有するリソースを選択してください",
            )
    result = ReviewSettings(id=profile_id or uuid4().hex, **data.model_dump())
    await adb.cypher_query(
        """
        MATCH (user:User {uid:$uid})
        SET user.review_settings_revision = coalesce(user.review_settings_revision, 0)+1
        WITH user
        MERGE (user)-[:REVIEW_SETTINGS]->(s:ReviewSettings {id:$id})
        SET s.config=$config
        """,
        params={"uid": uid, "id": result.id, "config": result.model_dump_json()},
    )
    return result


async def today_settings(
    user_id: UUIDy,
    profile_id: str,
    day: date,
) -> ReviewSettings:
    """知識・クイズで同じ日の設定を共有し、編集は翌日から反映する."""
    config = await get_settings(user_id, profile_id)
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid:$uid})
        SET user.review_settings_revision = coalesce(user.review_settings_revision, 0)+1
        WITH user
        MERGE (user)-[:REVIEW_DAY]->(s:ReviewDaySettings {id:$id, day:date($day)})
        ON CREATE SET s.config=$config
        RETURN s.config
        """,
        params={
            "uid": to_uuid(user_id).hex,
            "id": profile_id,
            "day": day.isoformat(),
            "config": config.model_dump_json(),
        },
    )
    await prune_daily(to_uuid(user_id).hex)
    return ReviewSettings.model_validate_json(rows[0][0])


async def reset_settings(
    user_id: UUIDy,
    profile_id: str,
    *,
    delete: bool = False,
) -> None:
    """明示的な再作成でのみ今日の設定・セットを破棄する."""
    await get_settings(user_id, profile_id)
    if delete and profile_id == "default":
        raise HTTPException(status_code=400, detail="標準設定は削除できません")
    await adb.cypher_query(
        """
        MATCH (user:User {uid:$uid})
        SET user.review_settings_revision = coalesce(user.review_settings_revision, 0)+1
        WITH user
        OPTIONAL MATCH (user)-[:REVIEW_DAY]->(day:ReviewDaySettings {id:$id})
        WHERE $delete OR day.day=date($today)
        WITH user, collect(day) AS days
        FOREACH (item IN days | DETACH DELETE item)
        WITH user
        OPTIONAL MATCH (user)-[:RECOMMENDATIONS]->(s:DailyRecommendation)
        WHERE s.scope IN $scopes AND ($delete OR s.day=date($today))
        WITH user, collect(s) AS sets
        FOREACH (item IN sets | DETACH DELETE item)
        WITH user
        OPTIONAL MATCH (user)-[:REVIEW_SETTINGS]->(setting:ReviewSettings {id:$id})
        WITH collect(setting) AS settings
        FOREACH (item IN CASE WHEN $delete THEN settings ELSE [] END |
            DETACH DELETE item)
        """,
        params={
            "uid": to_uuid(user_id).hex,
            "id": profile_id,
            "delete": delete,
            "today": datetime.now(TZ).date().isoformat(),
            "scopes": [
                settings_scope(profile_id, kind) for kind in ("tanbuns", "quizzes")
            ],
        },
    )
