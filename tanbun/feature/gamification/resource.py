"""リソースの知識構造と、ユーザー別の永続的な復習実績."""

from math import isqrt
from uuid import UUID

from neomodel import adb
from pydantic import BaseModel

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.gamification.domain import (
    LEVEL_CURVE,
    QUIZ_ANSWERED_XP,
    QUIZ_CORRECT_BONUS_XP,
    TANBUN_EXPOSURE_XP,
    level_threshold,
)


class ResourceXpLog(BaseModel):
    """削除・更新後にも残るXPの根拠."""

    source: str
    xp: int
    subject: str
    earned_on: str


class ResourceGrowth(BaseModel):
    """Powerと個人の復習レベルは独立した指標."""

    resource_id: UUID
    resource_name: str
    total_xp: int
    level: int
    current_level_xp: int
    xp_for_next_level: int
    power: int
    logic_count: int
    reference_count: int
    recent_xp: list[ResourceXpLog]
    last_reviewed_on: str | None = None
    exposure_xp: int = 0
    answer_xp: int = 0
    correct_bonus_xp: int = 0


class ResourceGrowthRules(BaseModel):
    """暫定のルールを表示と計算で共用する."""

    exposure_xp: int = TANBUN_EXPOSURE_XP
    answer_xp: int = QUIZ_ANSWERED_XP
    correct_bonus_xp: int = QUIZ_CORRECT_BONUS_XP
    level_curve: int = LEVEL_CURVE


class ResourceGrowthResult(BaseModel):
    """本人の進捗一覧と適用中のルール."""

    resources: list[ResourceGrowth]
    rules: ResourceGrowthRules


# Userへの書き込みロックで、同じ人の同時リクエストを直列化する。
# Resource/Quizへの辺は持たせず、削除しても実績のスナップショットを保持する。
EVENT_QUERY = """
    MATCH (user:User {uid: $user_id})
    SET user.resource_xp_revision = coalesce(user.resource_xp_revision, 0) + 1
    WITH user
    MERGE (event:ResourceXpEvent {key: $key})
    ON CREATE SET event.user_id = $user_id, event.resource_id = $resource_id,
        event.resource_name = $resource_name, event.source = $source,
        event.xp = $xp, event.subject = $subject, event.earned_on = $day
"""


async def record_resource_xp(
    user_id: UUIDy,
    resource_id: UUIDy,
    resource_name: str,
    *,
    subject_id: str,
    subject: str,
    day: str,
    source: str,
    xp: int,
) -> None:
    """同じ対象・種別・日の加点は一回だけ."""
    uid = to_uuid(user_id).hex
    await adb.cypher_query(
        EVENT_QUERY,
        params={
            "user_id": uid,
            "resource_id": to_uuid(resource_id).hex,
            "resource_name": resource_name,
            "subject": subject,
            "day": day,
            "source": source,
            "xp": xp,
            "key": f"{uid}:{subject_id}:{day}:{source}",
        },
    )


async def record_answer_xp(user_id: UUIDy, answer_id: UUIDy) -> None:
    """回答対象を保存時に解決し、参照切れ後も履歴を保持する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (:User {uid: $user_id})-[:ANSWER]->(answer:Answer {uid: $answer_id})
            -[:ANSWER_OF]->(quiz:Quiz)-[:QUIZ_TARGET]->(target:Sentence)
        MATCH (resource:Resource {uid: target.resource_uid})
        RETURN resource.uid, resource.title, target.val, quiz.uid,
            toString(date(answer.created)), answer.is_correct
        ORDER BY resource.uid, target.uid LIMIT 1
    """,
        params={"user_id": to_uuid(user_id).hex, "answer_id": to_uuid(answer_id).hex},
    )
    for resource_id, name, subject, quiz_id, day, correct in rows:
        await record_resource_xp(
            user_id,
            resource_id,
            name,
            subject_id=quiz_id,
            subject=subject,
            day=day,
            source="quiz_answer",
            xp=QUIZ_ANSWERED_XP,
        )
        if correct:
            await record_resource_xp(
                user_id,
                resource_id,
                name,
                subject_id=quiz_id,
                subject=subject,
                day=day,
                source="correct_bonus",
                xp=QUIZ_CORRECT_BONUS_XP,
            )


async def record_exposure_xp(user_id: UUIDy, sentence_id: UUIDy, day: str) -> None:
    """見たよの対象と内容をスナップショットとして保存する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (sentence:Sentence {uid: $sentence_id})
        MATCH (resource:Resource {uid: sentence.resource_uid})
        RETURN resource.uid, resource.title, sentence.val
    """,
        params={"sentence_id": to_uuid(sentence_id).hex},
    )
    for resource_id, name, subject in rows:
        await record_resource_xp(
            user_id,
            resource_id,
            name,
            subject_id=to_uuid(sentence_id).hex,
            subject=subject,
            day=day,
            source="tanbun_exposure",
            xp=TANBUN_EXPOSURE_XP,
        )


async def fetch_resource_growth(
    user_id: UUIDy,
    *,
    owned_only: bool = False,
) -> list[ResourceGrowth]:
    """現在の構造は再計算し、XPは保存した実績だけを集計する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (resource:Resource)
        WHERE EXISTS {
            MATCH (resource)-[:PARENT*0..]->()-[:OWNED]->(:User {uid: $user_id})
        } OR (NOT $owned_only AND EXISTS {
            MATCH (:ResourceXpEvent {user_id: $user_id, resource_id: resource.uid})
        })
        WITH DISTINCT resource
        CALL (resource) {
            MATCH (source:Sentence|Quoterm {resource_uid: resource.uid})
                -[edge:TO|REF|RESOLVED|QUOTERM]->(destination:Sentence|Quoterm)
            WHERE source <> destination AND destination.val <> '<<<not defined>>>'
            WITH DISTINCT CASE WHEN type(edge) = 'TO' THEN 'logic'
                ELSE 'reference' END AS kind,
                source.uid AS start, destination.uid AS end
            RETURN count(CASE WHEN kind = 'logic' THEN 1 END) AS logic_count,
                count(CASE WHEN kind = 'reference' THEN 1 END) AS reference_count
        }
        OPTIONAL MATCH (event:ResourceXpEvent {
            user_id: $user_id, resource_id: resource.uid
        })
        WITH resource, logic_count, reference_count, event
        ORDER BY event.earned_on DESC, event.key
        RETURN resource.uid, resource.title, logic_count, reference_count,
            coalesce(sum(event.xp), 0), collect(event)[0..10],
            coalesce(sum(CASE WHEN event.source = 'tanbun_exposure'
                THEN event.xp ELSE 0 END), 0),
            coalesce(sum(CASE WHEN event.source = 'quiz_answer'
                THEN event.xp ELSE 0 END), 0),
            coalesce(sum(CASE WHEN event.source = 'correct_bonus'
                THEN event.xp ELSE 0 END), 0)
        ORDER BY resource.title
    """,
        params={"user_id": to_uuid(user_id).hex, "owned_only": owned_only},
    )
    result = []
    for (
        resource_id,
        name,
        logic,
        reference,
        xp,
        events,
        exposure,
        answer,
        bonus,
    ) in rows:
        level = isqrt(xp // LEVEL_CURVE) + 1
        result.append(
            ResourceGrowth(
                resource_id=resource_id,
                resource_name=name,
                total_xp=xp,
                level=level,
                current_level_xp=xp - level_threshold(level),
                xp_for_next_level=level_threshold(level + 1) - level_threshold(level),
                power=logic + reference,
                logic_count=logic,
                reference_count=reference,
                last_reviewed_on=events[0]["earned_on"] if events else None,
                exposure_xp=exposure,
                answer_xp=answer,
                correct_bonus_xp=bonus,
                recent_xp=[
                    ResourceXpLog(
                        source=e["source"],
                        xp=e["xp"],
                        subject=e["subject"],
                        earned_on=e["earned_on"],
                    )
                    for e in events
                ],
            ),
        )
    return result
