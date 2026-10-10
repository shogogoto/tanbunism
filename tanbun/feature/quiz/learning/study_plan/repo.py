"""StudyPlanのrepo."""

from datetime import datetime
from uuid import UUID, uuid4

from neomodel import adb

from tanbun.feature.domain.datetime import TZ, neo4j_dt_validator
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.study_plan.domain import (
    StudyPlan,
    StudyPlanDraft,
)
from tanbun.feature.quiz.learning.study_plan.errors import (
    StudyPlanCreateError,
)

QUIZZES_PER_ADVENTURE_REGION = 5


async def fetch_study_plan(
    plan_id: UUIDy,
    user_id: UUIDy,
) -> StudyPlan | None:
    """所有者に紐づくStudyPlanを取得."""
    q = """
        MATCH (plan: StudyPlan {uid: $plan_id})
            -[:OWNED]->(:User {uid: $user_id})
        MATCH (plan)-[study:STUDY]->(resource: Resource)
        WITH plan, study, resource
        ORDER BY study.position ASC
        RETURN
            plan.uid,
            plan.name,
            plan.quiz_types,
            plan.n_quiz,
            plan.n_option,
            plan.created,
            COLLECT(resource.uid),
            plan.auto_resource_uid IS NOT NULL
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "plan_id": to_uuid(plan_id).hex,
            "user_id": to_uuid(user_id).hex,
        },
    )
    if not rows:
        return None

    (
        uid,
        name,
        quiz_types,
        n_quiz,
        n_option,
        created,
        resource_ids,
        default_resource_plan,
    ) = rows[0]
    return StudyPlan(
        uid=uid,
        name=name,
        resource_ids=resource_ids,
        quiz_types=[QuizType[quiz_type] for quiz_type in quiz_types],
        n_quiz=n_quiz,
        n_option=n_option,
        created=neo4j_dt_validator(created),
        default_resource_plan=default_resource_plan,
    )


async def list_study_plans(user_id: UUIDy) -> list[StudyPlan]:
    """ユーザーが所有するStudyPlanを新しい順に取得."""
    q = """
        MATCH (plan: StudyPlan)-[:OWNED]->(:User {uid: $user_id})
        RETURN plan.uid
        ORDER BY plan.created DESC, plan.uid ASC
    """
    rows, _ = await adb.cypher_query(
        q,
        params={"user_id": to_uuid(user_id).hex},
    )
    plans = []
    for row in rows:
        plan = await fetch_study_plan(row[0], user_id)
        if plan is not None:
            plans.append(plan)
    return plans


async def create_study_plan(
    user_id: UUIDy,
    draft: StudyPlanDraft,
) -> StudyPlan:
    """StudyPlanと対象resourceを永続化."""
    plan_id = uuid4()
    now = datetime.now(tz=TZ)
    resource_ids = list(dict.fromkeys(draft.resource_ids))
    q = """
        MATCH (user: User {uid: $user_id})
        MATCH (resource: Resource)
        WHERE resource.uid IN $resource_ids
        WITH user, COLLECT(DISTINCT resource) AS resources
        WHERE size(resources) = size($resource_ids)
        CREATE (plan: StudyPlan {
            uid: $plan_id,
            name: $name,
            quiz_types: $quiz_types,
            n_quiz: $n_quiz,
            n_option: $n_option,
            created: datetime($now)
        })-[:OWNED]->(user)
        WITH plan
        UNWIND range(0, size($resource_ids) - 1) AS position
        MATCH (resource: Resource {uid: $resource_ids[position]})
        CREATE (plan)-[:STUDY {position: position}]->(resource)
        RETURN DISTINCT plan.uid
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "plan_id": plan_id.hex,
            "user_id": to_uuid(user_id).hex,
            "resource_ids": [uid.hex for uid in resource_ids],
            "name": draft.name,
            "quiz_types": [quiz_type.name for quiz_type in draft.quiz_types],
            "n_quiz": draft.n_quiz,
            "n_option": draft.n_option,
            "now": now.isoformat(),
        },
    )
    if not rows:
        msg = "ユーザーまたは対象resourceが存在せずStudyPlanを作成できません"
        raise StudyPlanCreateError(msg)

    plan = await fetch_study_plan(plan_id, user_id)
    if plan is None:
        msg = "作成したStudyPlanを復元できません"
        raise StudyPlanCreateError(msg)
    return plan


async def ensure_default_resource_study_plan(
    user_id: UUIDy,
    resource_id: UUIDy,
    resource_name: str,
) -> StudyPlan:
    """Resourceごとの既定StudyPlanを重複なく用意する."""
    plan_id = uuid4()
    now = datetime.now(tz=TZ)
    q = """
        MATCH (user:User {uid: $user_id})
        MATCH (resource:Resource {uid: $resource_id})
        MERGE (plan:StudyPlan {auto_resource_uid: $resource_id})
        ON CREATE SET
            plan.uid = $plan_id,
            plan.name = $resource_name,
            plan.quiz_types = $quiz_types,
            plan.n_quiz = 5,
            plan.n_option = 4,
            plan.created = datetime($now)
        ON MATCH SET plan.quiz_types = CASE
            WHEN plan.quiz_types = ['TERM2SENT'] THEN $quiz_types
            ELSE plan.quiz_types
        END
        MERGE (plan)-[:OWNED]->(user)
        MERGE (plan)-[:STUDY {position: 0}]->(resource)
        RETURN plan.uid
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "plan_id": plan_id.hex,
            "user_id": to_uuid(user_id).hex,
            "resource_id": to_uuid(resource_id).hex,
            "resource_name": resource_name,
            "quiz_types": [quiz_type.name for quiz_type in QuizType],
            "now": now.isoformat(),
        },
    )
    if not rows:
        msg = "Resourceの既定StudyPlanを作成できません"
        raise StudyPlanCreateError(msg)
    plan = await fetch_study_plan(rows[0][0], user_id)
    if plan is None:
        msg = "Resourceの既定StudyPlanを復元できません"
        raise StudyPlanCreateError(msg)
    return plan


async def adventure_quiz_progress(
    user_id: UUIDy,
    resource_id: UUIDy,
) -> tuple[UUID | None, int]:
    """Resource既定Planの冒険向け準備済み領域数."""
    rows, _ = await adb.cypher_query(
        """MATCH (plan:StudyPlan {auto_resource_uid: $resource_id})
            -[:OWNED]->(:User {uid: $user_id})
        RETURN plan.uid, coalesce(plan.adventure_prepared_regions, 0)""",
        {
            "resource_id": to_uuid(resource_id).hex,
            "user_id": to_uuid(user_id).hex,
        },
    )
    return (to_uuid(rows[0][0]), rows[0][1]) if rows else (None, 0)


async def complete_adventure_quiz_region(
    user_id: UUIDy,
    plan_id: UUIDy,
    region: int,
) -> bool:
    """一つの達成領域の準備を記録し、推薦件数を5問広げる."""
    rows, _ = await adb.cypher_query(
        """MATCH (plan:StudyPlan {uid: $plan_id})
            -[:OWNED]->(:User {uid: $user_id})
        WITH plan, coalesce(plan.adventure_prepared_regions, 0) AS current
        WHERE current = $region
        SET plan.adventure_prepared_regions = $region + 1
        RETURN plan.uid""",
        {
            "plan_id": to_uuid(plan_id).hex,
            "user_id": to_uuid(user_id).hex,
            "region": region,
        },
    )
    return bool(rows)


async def update_study_plan(
    plan_id: UUIDy,
    user_id: UUIDy,
    draft: StudyPlanDraft,
) -> StudyPlan | None:
    """所有するStudyPlanの設定と対象resourceを置き換える."""
    resource_ids = list(dict.fromkeys(draft.resource_ids))
    q = """
        MATCH (plan: StudyPlan {uid: $plan_id})
            -[:OWNED]->(:User {uid: $user_id})
        MATCH (resource: Resource)
        WHERE resource.uid IN $resource_ids
        WITH plan, COLLECT(DISTINCT resource) AS resources
        WHERE size(resources) = size($resource_ids)
        SET
            plan.name = $name,
            plan.quiz_types = $quiz_types,
            plan.n_quiz = $n_quiz,
            plan.n_option = $n_option,
            plan.auto_resource_uid = CASE
                WHEN size($resource_ids) = 1
                    AND plan.auto_resource_uid = $resource_ids[0]
                THEN plan.auto_resource_uid
                ELSE null END
        WITH plan
        OPTIONAL MATCH (plan)-[old:STUDY]->()
        DELETE old
        WITH DISTINCT plan
        UNWIND range(0, size($resource_ids) - 1) AS position
        MATCH (resource: Resource {uid: $resource_ids[position]})
        CREATE (plan)-[:STUDY {position: position}]->(resource)
        RETURN DISTINCT plan.uid
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "plan_id": to_uuid(plan_id).hex,
            "user_id": to_uuid(user_id).hex,
            "resource_ids": [uid.hex for uid in resource_ids],
            "name": draft.name,
            "quiz_types": [quiz_type.name for quiz_type in draft.quiz_types],
            "n_quiz": draft.n_quiz,
            "n_option": draft.n_option,
        },
    )
    if not rows:
        return None
    return await fetch_study_plan(plan_id, user_id)


async def delete_study_plan(
    plan_id: UUIDy,
    user_id: UUIDy,
) -> bool:
    """所有するStudyPlanを削除."""
    q = """
        MATCH (plan: StudyPlan {uid: $plan_id})
            -[:OWNED]->(:User {uid: $user_id})
        WHERE plan.auto_resource_uid IS NULL
        DETACH DELETE plan
        RETURN count(*) AS deleted
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "plan_id": to_uuid(plan_id).hex,
            "user_id": to_uuid(user_id).hex,
        },
    )
    return bool(rows and rows[0][0])


async def count_prepared_quizzes(
    plan_id: UUIDy,
    user_id: UUIDy,
) -> int:
    """Plan対象内の壊れていない作成済みクイズを回答済みも含めて数える."""
    q = """
        MATCH (plan:StudyPlan {uid: $plan_id})-[:OWNED]->(
            user:User {uid: $user_id}
        )
        CALL (plan, user) {
            MATCH (plan)-[:STUDY]->(resource:Resource)
            MATCH (user)-[:LEARN]->(quiz:Quiz)-[:QUIZ_TARGET]->(
                sentence:Sentence
            )
            WHERE sentence.resource_uid = resource.uid
              AND quiz.quiz_type IN plan.quiz_types
              AND NOT EXISTS {
                  MATCH (quiz)-[:BROKEN_BY]->()
              }
            RETURN count(DISTINCT quiz) AS prepared_count
        }
        RETURN prepared_count
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "plan_id": to_uuid(plan_id).hex,
            "user_id": to_uuid(user_id).hex,
        },
    )
    return rows[0][0] if rows else 0


async def list_prepared_quiz_counts(
    user_id: UUIDy,
) -> dict[UUID, int]:
    """所有する全Planの作成済みクイズ数を回答済みも含めて一括取得する."""
    q = """
        MATCH (plan:StudyPlan)-[:OWNED]->(user:User {uid: $user_id})
        CALL (plan, user) {
            OPTIONAL MATCH (plan)-[:STUDY]->(resource:Resource)
            OPTIONAL MATCH (user)-[:LEARN]->(quiz:Quiz)-[:QUIZ_TARGET]->(
                sentence:Sentence
            )
            WHERE sentence.resource_uid = resource.uid
              AND quiz.quiz_type IN plan.quiz_types
              AND NOT EXISTS {
                  MATCH (quiz)-[:BROKEN_BY]->()
              }
            RETURN count(DISTINCT quiz) AS prepared_count
        }
        RETURN plan.uid, prepared_count
        ORDER BY plan.created DESC, plan.uid
    """
    rows, _ = await adb.cypher_query(
        q,
        params={"user_id": to_uuid(user_id).hex},
    )
    return {to_uuid(plan_id): count for plan_id, count in rows}
