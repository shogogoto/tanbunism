"""Quiz管理repo."""

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.entry.mapper import MResource
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.management.domain import (
    BrokenQuizReference,
    QuizReattachmentResult,
    QuizReport,
    QuizReportReason,
    QuizResourceStatus,
    SentenceQuizStatus,
)


async def report_quiz_issue(
    user_id: UUIDy,
    quiz_id: UUIDy,
    reason: QuizReportReason,
    detail: str | None,
) -> bool:
    """同じユーザーから同じQuizへの報告をupsertする."""
    query = """
        MATCH (user:User {uid: $user_id})
        MATCH (quiz:Quiz {uid: $quiz_id})
        MERGE (user)-[:REPORT]->(report:QuizReport {key: $key})
            -[:REPORT_OF]->(quiz)
        ON CREATE SET report.uid = randomUUID(), report.created = datetime()
        SET report.reason = $reason,
            report.detail = $detail,
            report.updated = datetime()
        RETURN report.uid
    """
    rows, _ = await adb.cypher_query(
        query,
        params={
            "user_id": to_uuid(user_id).hex,
            "quiz_id": to_uuid(quiz_id).hex,
            "key": f"{to_uuid(user_id).hex}:{to_uuid(quiz_id).hex}",
            "reason": reason.value,
            "detail": detail.strip() if detail else None,
        },
    )
    return bool(rows)


async def list_reported_created_quizzes(user_id: UUIDy) -> list[QuizReport]:
    """自分が作成したQuizに届いた不備報告を集約する."""
    query = """
        MATCH (:User {uid: $user_id})-[:CREATE]->(quiz:Quiz)
        MATCH (:User)-[:REPORT]->(report:QuizReport)-[:REPORT_OF]->(quiz)
        OPTIONAL MATCH (quiz)-[:QUIZ_TARGET]->(target)
        OPTIONAL MATCH (resource:Resource {uid: target.resource_uid})
        WITH quiz, target, resource, collect(report) AS reports
        UNWIND reports AS latest
        WITH quiz, target, resource, reports, latest
        ORDER BY latest.updated DESC
        WITH quiz, target, resource, reports, collect(latest)[0] AS latest
        RETURN quiz.uid, latest.reason, latest.detail, size(reports),
            target.resource_uid, resource.title, latest.updated
        ORDER BY latest.updated DESC, quiz.uid
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"user_id": to_uuid(user_id).hex},
    )
    return [
        QuizReport(
            quiz_id=quiz_id,
            reason=QuizReportReason(reason),
            detail=detail,
            report_count=report_count,
            resource_id=resource_id,
            resource_name=resource_name,
            updated_at=updated_at,
        )
        for (
            quiz_id,
            reason,
            detail,
            report_count,
            resource_id,
            resource_name,
            updated_at,
        ) in rows
    ]


async def list_broken_created_quiz_references(
    user_id: UUIDy,
) -> list[BrokenQuizReference]:
    """自分が作成したQuizの退役単文参照を一覧する."""
    query = """
        MATCH (:User {uid: $user_id})-[:CREATE]->(quiz:Quiz)
            -[:BROKEN_BY]->(retired:RetiredSentence)
        MATCH (quiz)-[source:QUIZ_TARGET|QUIZ_OPTION|CORRECT]->(retired)
        OPTIONAL MATCH (resource:Resource {uid: retired.resource_uid})
        RETURN quiz.uid, quiz.quiz_type, retired.uid, retired.val,
            retired.resource_uid,
            coalesce(retired.resource_name, resource.title) AS resource_name,
            collect(DISTINCT type(source)) AS roles, retired.retired_at
        ORDER BY retired.retired_at DESC, quiz.uid, retired.uid
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"user_id": to_uuid(user_id).hex},
    )
    return [
        BrokenQuizReference(
            quiz_id=quiz_id,
            quiz_type=QuizType(quiz_type.lower()),
            retired_sentence_id=retired_id,
            retired_value=retired_value,
            resource_id=resource_id,
            resource_name=resource_name,
            roles=roles,
            retired_at=retired_at,
        )
        for (
            quiz_id,
            quiz_type,
            retired_id,
            retired_value,
            resource_id,
            resource_name,
            roles,
            retired_at,
        ) in rows
    ]


async def reattach_created_quiz_sentence(
    user_id: UUIDy,
    quiz_id: UUIDy,
    retired_uid: UUIDy,
    replacement_uid: UUIDy,
) -> QuizReattachmentResult | None:
    """作成者本人のQuiz 1件だけを現行単文へ付け替える."""
    query = """
        MATCH (:User {uid: $user_id})-[:CREATE]->(quiz:Quiz {uid: $quiz_id})
            -[:BROKEN_BY]->(retired:RetiredSentence {uid: $retired_uid})
        MATCH (replacement:Sentence {uid: $replacement_uid})
        CALL (quiz, retired, replacement) {
            OPTIONAL MATCH (quiz)-[old:QUIZ_TARGET]->(retired)
            FOREACH (_ IN CASE WHEN old IS NULL THEN [] ELSE [1] END |
                MERGE (quiz)-[:QUIZ_TARGET]->(replacement)
                DELETE old
            )
            RETURN count(old) AS targets
        }
        CALL (quiz, retired, replacement) {
            OPTIONAL MATCH (quiz)-[old:QUIZ_OPTION]->(retired)
            FOREACH (_ IN CASE WHEN old IS NULL THEN [] ELSE [1] END |
                MERGE (quiz)-[:QUIZ_OPTION]->(replacement)
                DELETE old
            )
            RETURN count(old) AS options
        }
        CALL (quiz, retired, replacement) {
            OPTIONAL MATCH (quiz)-[old:CORRECT]->(retired)
            FOREACH (_ IN CASE WHEN old IS NULL THEN [] ELSE [1] END |
                MERGE (quiz)-[:CORRECT]->(replacement)
                DELETE old
            )
            RETURN count(old) AS corrects
        }
        MATCH (quiz)-[broken:BROKEN_BY]->(retired)
        DELETE broken
        WITH DISTINCT retired, targets, options, corrects
        OPTIONAL MATCH (:Quiz)-[remaining:QUIZ_TARGET|QUIZ_OPTION|CORRECT]
            ->(retired)
        OPTIONAL MATCH (answer:Answer)-[:SELECT]->(retired)
        WITH retired, targets, options, corrects,
            count(DISTINCT remaining) + count(DISTINCT answer) > 0 AS retained
        FOREACH (_ IN CASE WHEN retained THEN [] ELSE [1] END |
            DETACH DELETE retired
        )
        RETURN targets, options, corrects, retained
    """
    rows, _ = await adb.cypher_query(
        query,
        params={
            "user_id": to_uuid(user_id).hex,
            "quiz_id": to_uuid(quiz_id).hex,
            "retired_uid": to_uuid(retired_uid).hex,
            "replacement_uid": to_uuid(replacement_uid).hex,
        },
    )
    if not rows:
        return None
    targets, options, corrects, retained = rows[0]
    return QuizReattachmentResult(
        quiz_targets=targets,
        quiz_options=options,
        quiz_corrects=corrects,
        retained=retained,
    )


async def list_created_quiz_resource_statuses(
    user_id: UUIDy,
) -> list[QuizResourceStatus]:
    """作成済みQuizをResourceごとに集計."""
    q = """
        MATCH (:User {uid: $user_id})-[:CREATE]->(quiz: Quiz)
            -[:QUIZ_TARGET]->(target: Sentence)
        MATCH (resource: Resource {uid: target.resource_uid})
        WHERE NOT EXISTS {
            MATCH (quiz)-[:BROKEN_BY]->()
        }
        WITH resource, quiz.quiz_type AS quiz_type, COUNT(quiz) AS count,
            MAX(quiz.created) AS type_last_created
        ORDER BY quiz_type
        WITH resource,
            COLLECT([quiz_type, count]) AS counts,
            SUM(count) AS total,
            MAX(type_last_created) AS last_created
        ORDER BY last_created DESC, resource.title
        RETURN resource, counts, total, last_created
    """
    rows, _ = await adb.cypher_query(
        q,
        params={"user_id": to_uuid(user_id).hex},
    )
    return [
        QuizResourceStatus(
            resource=MResource.freeze_dict(resource),
            quiz_counts={
                QuizType(quiz_type.lower()): count for quiz_type, count in counts
            },
            total_quizzes=total,
            last_created_at=last_created,
        )
        for resource, counts, total, last_created in rows
    ]


async def list_created_quiz_sentence_statuses(
    user_id: UUIDy,
    resource_id: UUIDy,
) -> list[SentenceQuizStatus]:
    """Resource内の単文を対象に作成したQuizを集計."""
    q = """
        MATCH (:User {uid: $user_id})-[:CREATE]->(quiz: Quiz)
            -[:QUIZ_TARGET]->(target: Sentence {resource_uid: $resource_id})
        WHERE NOT EXISTS {
            MATCH (quiz)-[:BROKEN_BY]->()
        }
        WITH target, quiz.quiz_type AS quiz_type, COUNT(quiz) AS count
        ORDER BY quiz_type
        WITH target, COLLECT([quiz_type, count]) AS counts, SUM(count) AS total
        ORDER BY target.uid
        RETURN target.uid, counts, total
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "user_id": to_uuid(user_id).hex,
            "resource_id": to_uuid(resource_id).hex,
        },
    )
    return [
        SentenceQuizStatus(
            sentence_id=sentence_id,
            quiz_counts={
                QuizType(quiz_type.lower()): count for quiz_type, count in counts
            },
            total_quizzes=total,
        )
        for sentence_id, counts, total in rows
    ]


async def delete_created_quiz(
    quiz_id: UUIDy,
    user_id: UUIDy,
) -> bool:
    """作成者本人のQuizと、それに対するAnswerを削除."""
    q = """
        MATCH (:User {uid: $user_id})-[:CREATE]->(quiz: Quiz {uid: $quiz_id})
        OPTIONAL MATCH (answer: Answer)-[:ANSWER_OF]->(quiz)
        OPTIONAL MATCH (report:QuizReport)-[:REPORT_OF]->(quiz)
        WITH quiz,
            [answer IN COLLECT(DISTINCT answer) WHERE answer IS NOT NULL] AS answers,
            [report IN COLLECT(DISTINCT report) WHERE report IS NOT NULL] AS reports
        FOREACH (answer IN answers | DETACH DELETE answer)
        FOREACH (report IN reports | DETACH DELETE report)
        DETACH DELETE quiz
        RETURN 1 AS deleted
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "quiz_id": to_uuid(quiz_id).hex,
            "user_id": to_uuid(user_id).hex,
        },
    )
    return bool(rows)


async def delete_created_quizzes(
    quiz_ids: list[UUIDy],
    user_id: UUIDy,
) -> tuple[int, int]:
    """作成者本人のQuiz群と、それらに対するAnswerを一括削除."""
    unique_ids = list(dict.fromkeys(to_uuid(quiz_id).hex for quiz_id in quiz_ids))
    q = """
        UNWIND $quiz_ids AS quiz_id
        OPTIONAL MATCH (:User {uid: $user_id})-[:CREATE]->(quiz:Quiz {uid: quiz_id})
        CALL (quiz) {
            OPTIONAL MATCH (answer:Answer)-[:ANSWER_OF]->(quiz)
            RETURN [item IN collect(DISTINCT answer) WHERE item IS NOT NULL]
                AS answers
        }
        CALL (quiz) {
            OPTIONAL MATCH (report:QuizReport)-[:REPORT_OF]->(quiz)
            RETURN [item IN collect(DISTINCT report) WHERE item IS NOT NULL]
                AS reports
        }
        WITH quiz, answers, reports
        WHERE quiz IS NOT NULL
        WITH collect(DISTINCT quiz) AS quizzes,
            reduce(all = [], items IN collect(answers) | all + items) AS answers,
            reduce(all = [], items IN collect(reports) | all + items) AS reports
        FOREACH (answer IN answers | DETACH DELETE answer)
        FOREACH (report IN reports | DETACH DELETE report)
        FOREACH (quiz IN quizzes | DETACH DELETE quiz)
        RETURN size(quizzes), size(answers)
    """
    rows, _ = await adb.cypher_query(
        q,
        params={"quiz_ids": unique_ids, "user_id": to_uuid(user_id).hex},
    )
    return (rows[0][0], rows[0][1]) if rows else (0, 0)
