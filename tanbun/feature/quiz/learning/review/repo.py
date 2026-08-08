"""復習対象クイズのrepo."""

from uuid import UUID

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.parsing.sysnet.sysnode import DUMMY_SENTENCE
from tanbun.feature.quiz.domain.parts import QuizType


async def fetch_unattempted_quiz_ids(
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
    limit: int,
) -> list[UUID]:
    """ユーザーが一度も回答していない既存クイズを古い順に取得."""
    if limit < 0:
        msg = "limitは0以上を指定してください"
        raise ValueError(msg)

    q = """
        MATCH (user: User {uid: $user_id})
            -[:LEARN]->(quiz: Quiz {
                quiz_type: $quiz_type,
                is_link_broken: false
            })-[:QUIZ_TARGET]->(
                :Sentence {resource_uid: $resource_id}
            )
        WHERE NOT EXISTS {
            MATCH (user)-[:ANSWER]->(:Answer)-[:ANSWER_OF]->(quiz)
        }
          AND NOT EXISTS {
            MATCH (quiz)-[:QUIZ_TARGET|QUIZ_OPTION|CORRECT]->(dummy: Sentence)
            WHERE dummy.val = $dummy_sentence
          }
        RETURN quiz.uid
        ORDER BY quiz.created ASC, quiz.uid ASC
        LIMIT $limit
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "resource_id": to_uuid(resource_id).hex,
            "user_id": to_uuid(user_id).hex,
            "quiz_type": quiz_type.name,
            "limit": limit,
            "dummy_sentence": DUMMY_SENTENCE,
        },
    )
    return [to_uuid(row[0]) for row in rows]
