"""クイズと回答の一覧取得repo.

時系列順
回答が多い順
正答率が低い順など
"""

from collections.abc import Iterable
from datetime import datetime

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.quiz.domain.answer import (
    Answer,
    AnswerHistoryItem,
    AnswerHistoryResult,
    Answers,
)
from tanbun.feature.quiz.domain.collections import (
    ReadableQuizResult,
    ReadableQuizzes,
)
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.management.domain import (
    ManagedQuiz,
    ManagedQuizResult,
)
from tanbun.feature.quiz.repo.restore import restore_quiz_sources
from tanbun.feature.repo.cypher import Paging


async def _to_result(total: int, ids: list[str]) -> ReadableQuizResult:
    srcs = await restore_quiz_sources(ids)
    return ReadableQuizResult(
        data=ReadableQuizzes(root=[src.to_readable() for src in srcs]),
        total=total,
    )


async def list_quiz_by_user_ids(
    user_uids: Iterable[UUIDy],
    paging: Paging = Paging(),
    resource_ids: Iterable[UUIDy] | None = None,
    sentence_ids: Iterable[UUIDy] | None = None,
) -> ReadableQuizResult:
    """特定ユーザーのクイズを列挙する."""
    q = f"""
        UNWIND $user_uids AS user_uid
        MATCH (u: User {{uid: user_uid}})
        MATCH (quiz: Quiz)<-[:CREATE]-(u)
        MATCH (quiz)-[:QUIZ_TARGET]->(target: Sentence)
        WHERE $resource_ids IS NULL OR target.resource_uid IN $resource_ids
        WITH quiz, target
        WHERE $sentence_ids IS NULL OR target.uid IN $sentence_ids
        // 新しい順
        ORDER BY quiz.created DESC
        WITH COLLECT(quiz.uid) as quiz_ids
        {paging.return_stmt("quiz_ids")}
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "user_uids": [to_uuid(uid).hex for uid in user_uids],
            "resource_ids": (
                [to_uuid(uid).hex for uid in resource_ids]
                if resource_ids is not None
                else None
            ),
            "sentence_ids": (
                [to_uuid(uid).hex for uid in sentence_ids]
                if sentence_ids is not None
                else None
            ),
            **paging.params,
        },
    )
    return await _to_result(*rows[0])


async def search_created_quizzes(
    user_id: UUIDy,
    paging: Paging = Paging(),
    *,
    resource_id: UUIDy | None = None,
    sentence_id: UUIDy | None = None,
    quiz_types: Iterable[QuizType] | None = None,
    answered: bool | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    min_accuracy: float | None = None,
    max_accuracy: float | None = None,
) -> ManagedQuizResult:
    """作成Quizを回答状況・作成日時・正答率で検索."""
    q = f"""
        MATCH (:User {{uid: $user_id}})-[:CREATE]->(quiz: Quiz)
        MATCH (quiz)-[:QUIZ_TARGET]->(target: Sentence)
        WHERE ($resource_id IS NULL OR target.resource_uid = $resource_id)
          AND ($sentence_id IS NULL OR target.uid = $sentence_id)
          AND ($quiz_types IS NULL OR quiz.quiz_type IN $quiz_types)
          AND ($created_from IS NULL OR quiz.created >= datetime($created_from))
          AND ($created_to IS NULL OR quiz.created <= datetime($created_to))
        OPTIONAL MATCH (:User {{uid: $user_id}})-[:ANSWER]->(answer: Answer)
            -[:ANSWER_OF]->(quiz)
        WITH
            quiz,
            COUNT(answer) AS attempts,
            COUNT(CASE WHEN answer.is_correct THEN 1 END) AS corrects,
            MAX(answer.created) AS last_attempted_at
        WITH
            quiz,
            attempts,
            corrects,
            last_attempted_at,
            CASE
                WHEN attempts = 0 THEN NULL
                ELSE toFloat(corrects) / attempts
            END AS accuracy
        WHERE ($answered IS NULL OR (attempts > 0) = $answered)
          AND ($min_accuracy IS NULL OR accuracy >= $min_accuracy)
          AND ($max_accuracy IS NULL OR accuracy <= $max_accuracy)
        ORDER BY quiz.created DESC, quiz.uid ASC
        WITH COLLECT({{
            quiz_id: quiz.uid,
            attempts: attempts,
            corrects: corrects,
            accuracy: accuracy,
            last_attempted_at: last_attempted_at
        }}) AS records
        {paging.return_stmt("records")}
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "user_id": to_uuid(user_id).hex,
            "resource_id": to_uuid(resource_id).hex if resource_id else None,
            "sentence_id": to_uuid(sentence_id).hex if sentence_id else None,
            "quiz_types": (
                [quiz_type.name for quiz_type in quiz_types]
                if quiz_types is not None
                else None
            ),
            "answered": answered,
            "created_from": created_from.isoformat() if created_from else None,
            "created_to": created_to.isoformat() if created_to else None,
            "min_accuracy": min_accuracy,
            "max_accuracy": max_accuracy,
            **paging.params,
        },
    )
    total, records = rows[0]
    sources = await restore_quiz_sources([record["quiz_id"] for record in records])
    source_by_id = {source.quiz_id.hex: source for source in sources}
    return ManagedQuizResult(
        total=total,
        data=[
            ManagedQuiz(
                quiz=source_by_id[record["quiz_id"]].to_readable(),
                attempts=record["attempts"],
                corrects=record["corrects"],
                accuracy=record["accuracy"],
                last_attempted_at=record["last_attempted_at"],
            )
            for record in records
        ],
    )


async def list_learning_quizzes(
    user_uid: UUIDy,
    paging: Paging = Paging(),
) -> ReadableQuizResult:
    """ユーザーの学習対象クイズを新しい順に列挙."""
    q = f"""
        MATCH (:User {{uid: $user_uid}})-[:LEARN]->(quiz: Quiz)
        WITH DISTINCT quiz
        ORDER BY quiz.created DESC, quiz.uid ASC
        WITH COLLECT(quiz.uid) AS quiz_ids
        {paging.return_stmt("quiz_ids")}
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "user_uid": to_uuid(user_uid).hex,
            **paging.params,
        },
    )
    return await _to_result(*rows[0])


async def list_quiz_by_sentence_ids(
    sent_uids: Iterable[UUIDy],
    paging: Paging = Paging(),
) -> ReadableQuizResult:
    """特定単文に紐づくクイズを列挙する."""
    q = f"""
        UNWIND $sent_uids AS sent_uid
        MATCH (s: Sentence {{uid: sent_uid}})
        MATCH (quiz: Quiz)-[:QUIZ_TARGET]->(s)
        // 新しい順
        ORDER BY quiz.created DESC
        WITH COLLECT(quiz.uid) as quiz_ids
        {paging.return_stmt("quiz_ids")}
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "sent_uids": [to_uuid(uid).hex for uid in sent_uids],
            **paging.params,
        },
    )
    return await _to_result(*rows[0])


# クイズに関連するクイズを返す
#   回答した選択肢の単文に関するクイズを作る
async def list_quiz_by_optioned(
    quiz_uids: list[UUIDy],
    paging: Paging = Paging(),
):
    """クイズのオプションにあるクイズを列挙する.

    オプションのオプション ... みたいな関係を取るには
    """
    q = """
        MATCH (quiz: Quiz {uid: $quiz_uid})
        OPTIONAL MATCH p = SHORTEST 1 (quiz)-[:!QUIZ_OPTION|QUIZ_TARGET]-*(opt)
        RETURN
            COLLECT(opt.uid) AS options
            , COLLECT(p) AS paths
    """
    rows, _ = await adb.cypher_query(q, params={"quiz_uid": to_uuid(quiz_uids).hex})
    return await _to_result(*rows[0])


# クイズの詳細で表示するくらいだと思う
async def list_answers(
    quiz_uids: list[UUIDy],
    user_uid: UUIDy | None = None,
) -> Answers:
    """クイズに対する回答一覧."""
    q = """
        UNWIND $quiz_uids AS qid
        // OPTIONAL MATCH (u: User {uid: $user_uid})
        MATCH (quiz: Quiz {uid: qid})
            <-[:ANSWER_OF]-(ans: Answer)
        // ユーザー情報を取得しつつ、user_uid が指定されていればフィルタリング
        MATCH (u: User)-[:ANSWER]->(ans)
        WHERE $user_uid IS NULL OR u.uid = $user_uid

        OPTIONAL MATCH (ans)-[:SELECT]->(s: Sentence)
        RETURN qid
            , ans
            , u
            , COLLECT(DISTINCT s.uid) AS selected
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "quiz_uids": [to_uuid(qid).hex for qid in quiz_uids],
            "user_uid": to_uuid(user_uid).hex if user_uid else None,
        },
    )

    # srcs = await restore_quiz_sources(quiz_uids)
    # rqs = {s.quiz_id.hex: build_readable(s) for s in srcs}
    # anss = {k: [] for k in rqs}
    ls = []
    for row in rows:
        qid, ans_, user, selected = row
        ans = Answer(
            answer_uid=ans_.get("uid"),
            quiz_uid=qid,
            selected=selected,
            who=user.get("uid"),
            is_correct=ans_.get("is_correct"),
            created=ans_.get("created"),
        )
        ls.append(ans)
    return Answers(root=ls)


async def list_answer_history(
    user_uid: UUIDy,
    paging: Paging = Paging(),
    *,
    is_correct: bool | None = None,
    quiz_type: QuizType | None = None,
    resource_id: UUIDy | None = None,
) -> AnswerHistoryResult:
    """認証ユーザーの回答を新しい順に一覧取得."""
    q = f"""
        MATCH (user: User {{uid: $user_uid}})-[:ANSWER]->(answer: Answer)
            -[:ANSWER_OF]->(quiz: Quiz)-[:QUIZ_TARGET]->(target: Sentence)
        WHERE ($is_correct IS NULL OR answer.is_correct = $is_correct)
          AND ($quiz_type IS NULL OR quiz.quiz_type = $quiz_type)
          AND ($resource_id IS NULL OR target.resource_uid = $resource_id)
        OPTIONAL MATCH (answer)-[:SELECT]->(selected: Sentence)
        WITH
            answer,
            quiz,
            target.resource_uid AS resource_id,
            COLLECT(DISTINCT selected.uid) AS selected_ids
        ORDER BY answer.created DESC, answer.uid ASC
        WITH COLLECT({{
            answer_uid: answer.uid,
            quiz_id: quiz.uid,
            quiz_type: quiz.quiz_type,
            resource_id: resource_id,
            selected: selected_ids,
            is_correct: answer.is_correct,
            created: answer.created
        }}) AS records
        {paging.return_stmt("records")}
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "user_uid": to_uuid(user_uid).hex,
            "is_correct": is_correct,
            "quiz_type": quiz_type.name if quiz_type else None,
            "resource_id": to_uuid(resource_id).hex if resource_id else None,
            **paging.params,
        },
    )
    total, records = rows[0]
    quiz_ids = list(dict.fromkeys(record["quiz_id"] for record in records))
    sources = await restore_quiz_sources(quiz_ids)
    source_by_id = {source.quiz_id.hex: source for source in sources}
    return AnswerHistoryResult(
        total=total,
        data=[
            AnswerHistoryItem(
                answer=Answer(
                    answer_uid=record["answer_uid"],
                    quiz_uid=record["quiz_id"],
                    selected=record["selected"],
                    who=to_uuid(user_uid),
                    is_correct=record["is_correct"],
                    created=record["created"],
                ),
                quiz_type=record["quiz_type"],
                resource_id=record["resource_id"],
                quiz=source_by_id[record["quiz_id"]].to_readable(),
            )
            for record in records
        ],
    )
