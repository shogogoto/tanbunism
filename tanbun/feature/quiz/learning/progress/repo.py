"""クイズ学習の進捗repo."""

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.parsing.sysnet.sysnode import DUMMY_SENTENCE
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.progress.domain import (
    QuizAttemptRate,
    QuizCoverage,
    QuizPerformance,
)


async def fetch_coverage(
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
) -> QuizCoverage:
    """クイズ化された単文の割合."""
    eligible = "<-[:DEF]-(:Term)" if quiz_type.has_term else ""
    q = f"""
        MATCH (sent: Sentence {{resource_uid: $resource_id}})
            {eligible}
        WHERE sent.val <> $dummy_sentence
        OPTIONAL MATCH (user: User {{uid: $user_id}})
            -[:LEARN]->(quiz: Quiz {{
                quiz_type: $quiz_type,
                is_link_broken: false
            }})-[:QUIZ_TARGET]->(sent)
        RETURN
            COUNT(DISTINCT sent) AS eligible,
            COUNT(DISTINCT CASE WHEN quiz IS NOT NULL THEN sent END) AS covered
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "resource_id": to_uuid(resource_id).hex,
            "user_id": to_uuid(user_id).hex,
            "quiz_type": quiz_type.name,
            "dummy_sentence": DUMMY_SENTENCE,
        },
    )
    eligible_count, covered_count = rows[0]
    return QuizCoverage(
        resource_id=to_uuid(resource_id),
        user_id=to_uuid(user_id),
        quiz_type=quiz_type,
        eligible=eligible_count,
        covered=covered_count,
    )


async def fetch_attempt_rate(
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
) -> QuizAttemptRate:
    """用意された有効なクイズのうち回答したクイズの割合."""
    q = """
        MATCH (user: User {uid: $user_id})
            -[:LEARN]->(quiz: Quiz {
                quiz_type: $quiz_type,
                is_link_broken: false
            })-[:QUIZ_TARGET]->(
                :Sentence {resource_uid: $resource_id}
            )
        OPTIONAL MATCH (user)-[:ANSWER]->(answer: Answer)
            -[:ANSWER_OF]->(quiz)
        RETURN
            COUNT(DISTINCT quiz) AS available,
            COUNT(
                DISTINCT CASE WHEN answer IS NOT NULL THEN quiz END
            ) AS attempted
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "resource_id": to_uuid(resource_id).hex,
            "user_id": to_uuid(user_id).hex,
            "quiz_type": quiz_type.name,
        },
    )
    available, attempted = rows[0]
    return QuizAttemptRate(
        resource_id=to_uuid(resource_id),
        user_id=to_uuid(user_id),
        quiz_type=quiz_type,
        available=available,
        attempted=attempted,
    )


async def fetch_performance(
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
) -> QuizPerformance:
    """クイズへの回答数、正解数、正答率、最終回答日時を取得."""
    q = """
        MATCH (user: User {uid: $user_id})
        OPTIONAL MATCH (user)-[:LEARN]->(quiz: Quiz {
            quiz_type: $quiz_type,
            is_link_broken: false
        })-[:QUIZ_TARGET]->(
            :Sentence {resource_uid: $resource_id}
        )
        OPTIONAL MATCH (user)-[:ANSWER]->(answer: Answer)
            -[:ANSWER_OF]->(quiz)
        RETURN
            COUNT(answer) AS attempts,
            COUNT(
                CASE WHEN answer.is_correct THEN answer END
            ) AS corrects,
            MAX(answer.created) AS last_attempted_at
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "resource_id": to_uuid(resource_id).hex,
            "user_id": to_uuid(user_id).hex,
            "quiz_type": quiz_type.name,
        },
    )
    attempts, corrects, last_attempted_at = rows[0]
    return QuizPerformance(
        resource_id=to_uuid(resource_id),
        user_id=to_uuid(user_id),
        quiz_type=quiz_type,
        attempts=attempts,
        corrects=corrects,
        last_attempted_at=last_attempted_at,
    )
