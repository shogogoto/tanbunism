"""孤立単文を監査・掃除するNeo4j操作."""

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.entry.resource.repo.delete import delete_resource
from tanbun.feature.entry.resource.repo.retirement import (
    purge_orphaned_retired_sentences,
)
from tanbun.feature.media.repo import schedule_avatar_delete
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.tanbun.repo.cypher import STREAM

from .domain import (
    AdminBrokenQuiz,
    AdminResourceItem,
    AdminUserItem,
    DeleteAdminResourceResult,
    DeleteBrokenQuizzesResult,
    DeleteOrphanedTanbunsResult,
    DeleteUserResult,
    OrphanedTanbun,
    OrphanReason,
    ResourceDeletionImpact,
    TanbunIntegrityKind,
)

QUIZ_SENTENCE_RELS = "QUIZ_TARGET|QUIZ_OPTION|CORRECT"
LOCATION_RELS = f"{STREAM}"
SEMANTIC_LOCATION_RELS = "TO|EXAMPLE|NUM|BY"


def _has_resource_owner(sentence_var: str) -> str:
    """Resourceと所有者が存在する条件を返す."""
    return f"""EXISTS {{
        MATCH (resource:Resource {{uid: {sentence_var}.resource_uid}})
        MATCH (resource)-[:PARENT|OWNED]->*(owner:User)
    }}"""


def _has_location(sentence_var: str) -> str:
    """Resource本文からSentenceへ辿れる条件を返す."""
    return f"""
        EXISTS {{
            MATCH (resource:Resource {{uid: {sentence_var}.resource_uid}})
            MATCH (resource)-[:PARENT|OWNED]->*(owner:User)
            MATCH (resource)-[:{LOCATION_RELS}]->*({sentence_var})
        }}
        OR EXISTS {{
            MATCH (resource:Resource {{uid: {sentence_var}.resource_uid}})
            MATCH (resource)-[:PARENT|OWNED]->*(owner:User)
            MATCH (resource)-[:{LOCATION_RELS}]->*(upper:Sentence)
            MATCH (upper)-[:{SEMANTIC_LOCATION_RELS}]->*({sentence_var})
        }}
    """


def _integrity_predicate(sentence_var: str, kind: TanbunIntegrityKind) -> str:
    """実体の孤立と、Resource内の配置切れを区別して絞る."""
    has_owner = _has_resource_owner(sentence_var)
    if kind is TanbunIntegrityKind.ORPHANED:
        return f"NOT ({has_owner})"
    return f"({has_owner}) AND NOT ({_has_location(sentence_var)})"


async def list_orphaned_tanbuns(
    *,
    kind: TanbunIntegrityKind = TanbunIntegrityKind.ORPHANED,
    limit: int = 200,
) -> list[OrphanedTanbun]:
    """孤立または配置切れの現行Sentenceを参照状況付きで返す."""
    integrity_predicate = _integrity_predicate("sentence", kind)
    query = f"""
        MATCH (sentence:Sentence)
        WHERE {integrity_predicate}
        OPTIONAL MATCH (resource:Resource {{uid: sentence.resource_uid}})
        OPTIONAL MATCH (resource)-[:PARENT|OWNED]->*(owner:User)
        CALL (sentence) {{
            OPTIONAL MATCH (quiz:Quiz)-[:{QUIZ_SENTENCE_RELS}]->(sentence)
            RETURN count(DISTINCT quiz) AS quiz_count
        }}
        CALL (sentence) {{
            OPTIONAL MATCH (answer:Answer)-[:SELECT]->(sentence)
            RETURN count(DISTINCT answer) AS answer_count
        }}
        CALL (sentence) {{
            OPTIONAL MATCH (sentence)-[relationship]-()
            RETURN count(DISTINCT relationship) AS relationship_count
        }}
        WITH sentence, resource, head(collect(DISTINCT owner)) AS owner,
            quiz_count, answer_count, relationship_count
        RETURN sentence.uid, sentence.val, sentence.resource_uid,
            resource.title, owner.email,
            CASE
                WHEN resource IS NULL THEN 'missing_resource'
                WHEN owner IS NULL THEN 'missing_owner'
                ELSE 'missing_location'
            END AS reason,
            quiz_count, answer_count, relationship_count
        ORDER BY quiz_count + answer_count DESC, sentence.val, sentence.uid
        LIMIT $limit
    """
    rows, _ = await adb.cypher_query(query, params={"limit": limit})
    return [
        OrphanedTanbun(
            uid=uid,
            sentence=sentence,
            resource_uid=resource_uid,
            resource_name=resource_name,
            owner_email=owner_email,
            reason=OrphanReason(reason),
            quiz_reference_count=quiz_count,
            answer_reference_count=answer_count,
            relationship_count=relationship_count,
        )
        for (
            uid,
            sentence,
            resource_uid,
            resource_name,
            owner_email,
            reason,
            quiz_count,
            answer_count,
            relationship_count,
        ) in rows
    ]


async def delete_orphaned_tanbuns(
    sentence_ids: list[str],
    kind: TanbunIntegrityKind = TanbunIntegrityKind.ORPHANED,
) -> DeleteOrphanedTanbunsResult:
    """指定種別の不整合が残る単文だけを削除または退役させる."""
    integrity_predicate = _integrity_predicate("sentence", kind)
    query = f"""
        UNWIND $sentence_ids AS sentence_id
        OPTIONAL MATCH (sentence:Sentence {{uid: sentence_id}})
        WHERE sentence IS NOT NULL AND {integrity_predicate}
        CALL (sentence) {{
            OPTIONAL MATCH (quiz:Quiz)-[:{QUIZ_SENTENCE_RELS}]->(sentence)
            RETURN [item IN collect(DISTINCT quiz) WHERE item IS NOT NULL]
                AS quizzes
        }}
        CALL (sentence) {{
            OPTIONAL MATCH (answer:Answer)-[:SELECT]->(sentence)
            RETURN count(DISTINCT answer) AS answer_count
        }}
        WITH sentence_id, sentence, quizzes, answer_count,
            size(quizzes) > 0 OR answer_count > 0 AS must_retire
        FOREACH (quiz IN CASE WHEN sentence IS NOT NULL THEN quizzes ELSE [] END |
            MERGE (quiz)-[:BROKEN_BY]->(sentence)
        )
        FOREACH (_ IN CASE
            WHEN sentence IS NOT NULL AND must_retire THEN [1] ELSE [] END |
            REMOVE sentence:Sentence
            SET sentence:RetiredSentence, sentence.retired_at = datetime()
        )
        FOREACH (_ IN CASE
            WHEN sentence IS NOT NULL AND NOT must_retire THEN [1] ELSE [] END |
            DETACH DELETE sentence
        )
        RETURN
            count(CASE WHEN sentence IS NOT NULL AND NOT must_retire THEN 1 END)
                AS deleted_count,
            count(CASE WHEN sentence IS NOT NULL AND must_retire THEN 1 END)
                AS retired_count,
            count(CASE WHEN sentence IS NULL THEN 1 END) AS skipped_count
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"sentence_ids": list(dict.fromkeys(sentence_ids))},
    )
    deleted_count, retired_count, skipped_count = rows[0]
    return DeleteOrphanedTanbunsResult(
        deleted_count=deleted_count,
        retired_count=retired_count,
        skipped_count=skipped_count,
    )


async def list_broken_quizzes(*, limit: int = 200) -> list[AdminBrokenQuiz]:
    """管理者向けに参照切れQuizを一覧する."""
    query = """
        MATCH (quiz:Quiz)-[:BROKEN_BY]->(retired:RetiredSentence)
        OPTIONAL MATCH (owner:User)-[:CREATE]->(quiz)
        WITH quiz, collect(DISTINCT retired) AS retireds,
            head(collect(DISTINCT owner.email)) AS owner_email
        OPTIONAL MATCH (answer:Answer)-[:ANSWER_OF]->(quiz)
        WITH quiz, owner_email, size(retireds) AS broken_reference_count,
            count(DISTINCT answer) AS answer_count
        RETURN quiz.uid, quiz.quiz_type, owner_email,
            broken_reference_count, answer_count, quiz.created
        ORDER BY answer_count DESC, broken_reference_count DESC,
            quiz.created DESC, quiz.uid
        LIMIT $limit
    """
    rows, _ = await adb.cypher_query(query, params={"limit": limit})
    return [
        AdminBrokenQuiz(
            quiz_id=quiz_id,
            quiz_type=QuizType(quiz_type.lower()),
            owner_email=owner_email,
            broken_reference_count=broken_count,
            answer_count=answer_count,
            created=created,
        )
        for quiz_id, quiz_type, owner_email, broken_count, answer_count, created in rows
    ]


async def delete_broken_quizzes(
    quiz_ids: list[UUIDy],
) -> DeleteBrokenQuizzesResult:
    """参照切れQuizと回答履歴を管理者権限で削除する."""
    query = """
        UNWIND $quiz_ids AS quiz_id
        OPTIONAL MATCH (quiz:Quiz {uid: quiz_id})-[:BROKEN_BY]->()
        WITH DISTINCT quiz
        CALL (quiz) {
            OPTIONAL MATCH (answer:Answer)-[:ANSWER_OF]->(quiz)
            RETURN [item IN collect(DISTINCT answer) WHERE item IS NOT NULL]
                AS answers
        }
        WITH quiz, answers
        FOREACH (answer IN answers | DETACH DELETE answer)
        FOREACH (_ IN CASE WHEN quiz IS NULL THEN [] ELSE [1] END |
            DETACH DELETE quiz
        )
        RETURN count(quiz), sum(size(answers))
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"quiz_ids": list(dict.fromkeys(to_uuid(uid).hex for uid in quiz_ids))},
    )
    deleted_count, deleted_answer_count = rows[0]
    await purge_orphaned_retired_sentences()
    return DeleteBrokenQuizzesResult(
        deleted_count=deleted_count,
        deleted_answer_count=deleted_answer_count or 0,
    )


async def list_users() -> list[AdminUserItem]:
    """ユーザーを所有Resource数付きで返す."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User)
        OPTIONAL MATCH (resource:Resource)-[:PARENT|OWNED]->*(user)
        RETURN user.uid, user.email, user.display_name, user.username,
            user.is_active, user.is_superuser, user.created,
            count(DISTINCT resource) AS resource_count
        ORDER BY user.is_superuser DESC, user.created DESC, user.email
        """,
    )
    return [
        AdminUserItem(
            uid=uid,
            email=email,
            display_name=display_name,
            username=username,
            is_active=is_active,
            is_superuser=is_superuser,
            created=created,
            resource_count=resource_count,
        )
        for (
            uid,
            email,
            display_name,
            username,
            is_active,
            is_superuser,
            created,
            resource_count,
        ) in rows
    ]


async def update_user_status(
    user_uid: UUIDy,
    *,
    is_active: bool,
) -> AdminUserItem | None:
    """ユーザーの利用可否を変更する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_uid})
        SET user.is_active = $is_active
        WITH user
        OPTIONAL MATCH (resource:Resource)-[:PARENT|OWNED]->*(user)
        RETURN user.uid, user.email, user.display_name, user.username,
            user.is_active, user.is_superuser, user.created,
            count(DISTINCT resource) AS resource_count
        """,
        params={"user_uid": to_uuid(user_uid).hex, "is_active": is_active},
    )
    if not rows:
        return None
    row = rows[0]
    return AdminUserItem(
        uid=row[0],
        email=row[1],
        display_name=row[2],
        username=row[3],
        is_active=row[4],
        is_superuser=row[5],
        created=row[6],
        resource_count=row[7],
    )


async def update_user_password_hash(
    user_uid: UUIDy,
    *,
    hashed_password: str,
) -> bool:
    """ユーザーのパスワードハッシュだけを更新する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_uid})
        SET user.hashed_password = $hashed_password
        RETURN count(user)
        """,
        params={
            "user_uid": to_uuid(user_uid).hex,
            "hashed_password": hashed_password,
        },
    )
    return bool(rows and rows[0][0])


async def delete_user_account(user_uid: UUIDy) -> DeleteUserResult | None:
    """先に利用停止し、所有Resourceとユーザー固有データを削除する."""
    uid = to_uuid(user_uid)
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_uid})
        SET user.is_active = false
        WITH user
        OPTIONAL MATCH (resource:Resource)-[:PARENT|OWNED]->*(user)
        RETURN collect(DISTINCT resource.uid), user.avatar_url
        """,
        params={"user_uid": uid.hex},
    )
    if not rows:
        return None
    await schedule_avatar_delete(rows[0][1])
    resource_ids = [resource_id for resource_id in rows[0][0] if resource_id]
    for resource_id in resource_ids:
        await delete_resource(resource_id)

    counts, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_uid})
        CALL (user) {
            OPTIONAL MATCH (user)-[:CREATE]->(quiz:Quiz)
            OPTIONAL MATCH (answer:Answer)-[:ANSWER_OF]->(quiz)
            WITH [item IN collect(DISTINCT quiz) WHERE item IS NOT NULL]
                    AS quizzes,
                [item IN collect(DISTINCT answer) WHERE item IS NOT NULL]
                    AS quiz_answers
            OPTIONAL MATCH (user)-[:ANSWER]->(own_answer:Answer)
            RETURN quizzes, quiz_answers,
                [item IN collect(DISTINCT own_answer) WHERE item IS NOT NULL]
                    AS own_answers
        }
        WITH user, quizzes,
            reduce(all = quiz_answers, item IN own_answers |
                CASE WHEN item IN all THEN all ELSE all + item END
            ) AS answers
        WITH user, quizzes, answers, size(quizzes) AS quiz_count,
            size(answers) AS answer_count
        FOREACH (answer IN answers | DETACH DELETE answer)
        FOREACH (quiz IN quizzes | DETACH DELETE quiz)
        WITH user, quiz_count, answer_count
        OPTIONAL MATCH (owned)-[:OWNED]->(user)
        WHERE owned:StudyPlan OR owned:Notification OR owned:PushSubscription
        WITH user, quiz_count, answer_count,
            [item IN collect(DISTINCT owned) WHERE item IS NOT NULL] AS owned
        FOREACH (item IN owned | DETACH DELETE item)
        WITH user, quiz_count, answer_count
        OPTIONAL MATCH (account:Account)<-[:OAUTH]-(user)
        WITH user, quiz_count, answer_count,
            [item IN collect(DISTINCT account) WHERE item IS NOT NULL] AS accounts
        FOREACH (account IN accounts | DETACH DELETE account)
        WITH user, quiz_count, answer_count
        OPTIONAL MATCH (folder:Folder)-[:PARENT|OWNED]->*(user)
        WITH user, quiz_count, answer_count,
            [item IN collect(DISTINCT folder) WHERE item IS NOT NULL] AS folders
        FOREACH (folder IN folders | DETACH DELETE folder)
        WITH user, quiz_count, answer_count
        OPTIONAL MATCH (exposure:TanbunExposure {user_id: $user_uid})
        WITH user, quiz_count, answer_count,
            [item IN collect(DISTINCT exposure) WHERE item IS NOT NULL] AS exposures
        FOREACH (exposure IN exposures | DETACH DELETE exposure)
        WITH user, quiz_count, answer_count
        OPTIONAL MATCH (xp:ResourceXpEvent {user_id: $user_uid})
        WITH user, quiz_count, answer_count, collect(xp) AS xp_events
        FOREACH (item IN xp_events | DETACH DELETE item)
        WITH user, quiz_count, answer_count
        OPTIONAL MATCH (user)-[:RECOMMENDATIONS]->(s:DailyRecommendation)
        WITH user, quiz_count, answer_count, collect(s) AS recommendations
        FOREACH (item IN recommendations | DETACH DELETE item)
        WITH user, quiz_count, answer_count
        OPTIONAL MATCH (user)-[:REVIEW_SETTINGS|REVIEW_DAY]->(setting)
        WITH user, quiz_count, answer_count, collect(setting) AS settings
        FOREACH (item IN settings | DETACH DELETE item)
        DETACH DELETE user
        RETURN quiz_count, answer_count
        """,
        params={"user_uid": uid.hex},
    )
    if not counts:
        return None
    await purge_orphaned_retired_sentences()
    return DeleteUserResult(
        user_id=uid,
        deleted_resource_count=len(resource_ids),
        deleted_quiz_count=counts[0][0],
        deleted_answer_count=counts[0][1],
    )


async def list_user_resources(user_uid: UUIDy) -> list[AdminResourceItem] | None:
    """指定ユーザーが存在すれば、所有Resourceを返す."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_uid})
        OPTIONAL MATCH (resource:Resource)-[:PARENT|OWNED]->*(user)
        CALL (resource) {
            OPTIONAL MATCH (sentence:Sentence {resource_uid: resource.uid})
            RETURN count(DISTINCT sentence) AS sentence_count
        }
        RETURN resource.uid, resource.title, resource.updated, sentence_count
        ORDER BY resource.updated DESC, resource.title
        """,
        params={"user_uid": to_uuid(user_uid).hex},
    )
    if not rows:
        return None
    return [
        AdminResourceItem(
            uid=uid,
            name=name,
            updated_at=updated_at,
            sentence_count=sentence_count,
        )
        for uid, name, updated_at, sentence_count in rows
        if uid is not None
    ]


async def get_resource_deletion_impact(
    resource_uid: UUIDy,
) -> ResourceDeletionImpact | None:
    """Resource削除時に削除・退役するデータ数を調べる."""
    rows, _ = await adb.cypher_query(
        f"""
        MATCH (resource:Resource {{uid: $resource_uid}})
        MATCH (resource)-[:PARENT|OWNED]->*(owner:User)
        CALL (resource) {{
            OPTIONAL MATCH (sentence:Sentence {{resource_uid: resource.uid}})
            RETURN count(DISTINCT sentence) AS sentence_count
        }}
        CALL (resource) {{
            OPTIONAL MATCH (term:Term)-[:DEF]->(
                :Sentence {{resource_uid: resource.uid}}
            )
            RETURN count(DISTINCT term) AS term_count
        }}
        CALL (resource) {{
            OPTIONAL MATCH (quiz:Quiz)-[:{QUIZ_SENTENCE_RELS}]->(
                :Sentence {{resource_uid: resource.uid}}
            )
            RETURN count(DISTINCT quiz) AS quiz_count
        }}
        CALL (resource) {{
            OPTIONAL MATCH (answer:Answer)-[:SELECT]->(
                :Sentence {{resource_uid: resource.uid}}
            )
            RETURN count(DISTINCT answer) AS answer_count
        }}
        CALL (resource) {{
            OPTIONAL MATCH (sentence:Sentence {{resource_uid: resource.uid}})
            WHERE EXISTS {{
                MATCH (:Quiz)-[:{QUIZ_SENTENCE_RELS}]->(sentence)
            }} OR EXISTS {{
                MATCH (:Answer)-[:SELECT]->(sentence)
            }}
            RETURN count(DISTINCT sentence) AS retiring_sentence_count
        }}
        RETURN resource.uid, resource.title, owner.uid, owner.email,
            sentence_count, term_count, quiz_count, answer_count,
            retiring_sentence_count,
            sentence_count - retiring_sentence_count AS deleting_sentence_count
        """,
        params={"resource_uid": to_uuid(resource_uid).hex},
    )
    if not rows:
        return None
    row = rows[0]
    return ResourceDeletionImpact(
        resource_uid=row[0],
        resource_name=row[1],
        owner_uid=row[2],
        owner_email=row[3],
        sentence_count=row[4],
        term_count=row[5],
        quiz_count=row[6],
        answer_count=row[7],
        retiring_sentence_count=row[8],
        deleting_sentence_count=row[9],
    )


async def delete_user_resource(
    resource_uid: UUIDy,
) -> DeleteAdminResourceResult | None:
    """影響数を記録してから既存の安全なResource削除を実行する."""
    impact = await get_resource_deletion_impact(resource_uid)
    if impact is None:
        return None
    await delete_resource(resource_uid)
    return DeleteAdminResourceResult(
        resource_uid=impact.resource_uid,
        deleted_sentence_count=impact.deleting_sentence_count,
        retired_sentence_count=impact.retiring_sentence_count,
    )
