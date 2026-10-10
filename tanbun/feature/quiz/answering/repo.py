"""クイズ回答repo."""

from datetime import datetime
from uuid import UUID, uuid4

from neomodel import adb

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.gamification.resource import record_answer_xp
from tanbun.feature.notification.usecase import dispatch_saved_notifications
from tanbun.feature.quiz.domain.answer import Answer
from tanbun.feature.quiz.errors import AnswerFailedError
from tanbun.feature.quiz.repo.restore import restore_quiz_sources


async def fetch_is_correct(quiz_uid: UUID, selected_uids: list[str]) -> bool:
    """クイズの回答の正解・不正解の問い合わせ."""
    srcs = await restore_quiz_sources([quiz_uid])
    rq = srcs[0].to_readable()
    return rq.is_correct(selected_uids)


async def create_answer(
    quiz_uid: UUID,
    selected_uids: list[str],
    user_uid: UUIDy,  # 回答者idは必須にする。回答したければユーザー登録しろ、という導線
) -> Answer:
    """回答の永続化."""
    notifications = []
    async with adb.transaction:
        answer, notifications = await create_answer_in_transaction(
            quiz_uid,
            selected_uids,
            user_uid,
        )
    await dispatch_saved_notifications(user_uid, notifications)
    return answer


async def create_answer_in_transaction(
    quiz_uid: UUID,
    selected_uids: list[str],
    user_uid: UUIDy,
    *,
    is_correct: bool | None = None,
) -> tuple[Answer, list]:
    """呼び出し元のトランザクションで回答・XPを保存。通知はcommit後に配送する."""
    answer_uid = uuid4()
    now = datetime.now(tz=TZ)

    q = """
        MATCH (quiz: Quiz {uid: $quiz_uid})
            , (u: User {uid: $user_uid})
        CREATE (ans: Answer {
            uid: $answer_uid
            , created: datetime($now)
            , is_correct: $is_correct
        })-[:ANSWER_OF]->(quiz)
            , (ans)<-[:ANSWER]-(u)
        WITH ans, u
        UNWIND $selected_uids AS suid
        MATCH (s: Sentence {uid: suid})
        CREATE (ans)-[:SELECT]->(s)
        RETURN ans, u
    """

    if is_correct is None:
        is_correct = await fetch_is_correct(quiz_uid, selected_uids)
    notifications = []
    _rows, _ = await adb.cypher_query(
        q,
        params={
            "quiz_uid": quiz_uid.hex,
            "selected_uids": [to_uuid(u).hex for u in selected_uids],
            "answer_uid": answer_uid.hex,
            "now": now.isoformat(),
            "is_correct": is_correct,
            "user_uid": to_uuid(user_uid).hex,
        },
    )
    # UNWIND [] returns no rows even though the Answer was created (no-option quiz).
    exists, _ = await adb.cypher_query(
        "MATCH (a:Answer {uid:$uid}) RETURN a.uid",
        {"uid": answer_uid.hex},
    )
    if exists:
        notifications = await record_answer_xp(user_uid, answer_uid)

    if exists:
        return Answer(
            answer_uid=answer_uid,
            quiz_uid=quiz_uid,
            selected=selected_uids,
            who=to_uuid(user_uid),
            is_correct=is_correct,
            created=now,
        ), notifications

    msg = "回答の永続化失敗"
    raise AnswerFailedError(msg)
