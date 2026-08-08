"""学習用クイズの対象選択repo."""

from collections.abc import Iterable
from random import Random, SystemRandom
from uuid import UUID

from more_itertools import flatten
from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.parsing.sysnet.sysnode import DUMMY_SENTENCE
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.selection.domain import (
    QuizTargetOrder,
    QuizTargetPool,
)
from tanbun.feature.quiz.repo.restore import KNOWLEDGE_REL_TYPES
from tanbun.feature.tanbun.repo.clause import OrderBy
from tanbun.feature.tanbun.repo.cypher import q_stats


async def _fetch_coverage_sent_ids(
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
    *,
    covered: bool,
) -> list[UUID]:
    """coverage条件に合う単文IDを取得."""
    eligible = "<-[:DEF]-(:Term)" if quiz_type.has_term else ""
    not_ = "" if covered else "NOT "
    q = f"""
        MATCH (sent: Sentence {{resource_uid: $resource_id}})
            {eligible}
        WHERE sent.val <> $dummy_sentence
          AND {not_}EXISTS {{
            MATCH (user: User {{uid: $user_id}})
                -[:LEARN]->(quiz: Quiz {{
                    quiz_type: $quiz_type,
                    is_link_broken: false
                }})-[:QUIZ_TARGET]->(sent)
        }}
        RETURN DISTINCT sent.uid
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
    return list(flatten(rows))


async def fetch_uncovered_sent_ids(
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
) -> list[UUID]:
    """ユーザー向けの有効なクイズがない適格単文を取得."""
    return await _fetch_coverage_sent_ids(
        resource_id,
        user_id,
        quiz_type,
        covered=False,
    )


async def fetch_covered_sent_ids(
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
) -> list[UUID]:
    """ユーザー向けの有効なクイズを持つ適格単文を取得."""
    return await _fetch_coverage_sent_ids(
        resource_id,
        user_id,
        quiz_type,
        covered=True,
    )


async def fetch_uncovered_relation_pairs(
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
    *,
    limit: int,
    exclude_sent_ids: Iterable[UUIDy] | None = None,
) -> list[tuple[UUID, UUID]]:
    """未coverageの対象単文と、knowledge path上の正解単文を取得."""
    q = """
        MATCH (target: Sentence {resource_uid: $resource_id})
            -[:__KNOWLEDGE_REL_TYPES__]-(correct: Sentence)
        WHERE target.val <> $dummy_sentence
          AND correct.val <> $dummy_sentence
          AND NOT target.uid IN $exclude_sent_ids
          AND NOT EXISTS {
            MATCH (user: User {uid: $user_id})
                -[:LEARN]->(quiz: Quiz {
                    quiz_type: $quiz_type,
                    is_link_broken: false
                })-[:QUIZ_TARGET]->(target)
          }
        RETURN DISTINCT target.uid, correct.uid
        ORDER BY target.uid ASC, correct.uid ASC
        LIMIT $limit
    """.replace("__KNOWLEDGE_REL_TYPES__", KNOWLEDGE_REL_TYPES)
    rows, _ = await adb.cypher_query(
        q,
        params={
            "resource_id": to_uuid(resource_id).hex,
            "user_id": to_uuid(user_id).hex,
            "quiz_type": quiz_type.name,
            "exclude_sent_ids": [to_uuid(uid).hex for uid in (exclude_sent_ids or [])],
            "limit": limit,
            "dummy_sentence": DUMMY_SENTENCE,
        },
    )
    return [(to_uuid(target), to_uuid(correct)) for target, correct in rows]


async def fetch_sort_by_score(
    sent_ids: Iterable[UUID],
    *,
    desc: bool = True,
    limit: int | None = None,
) -> list[UUID]:
    """score順に並び替える."""
    if limit is not None and limit < 0:
        msg = "limitは0以上を指定してください"
        raise ValueError(msg)

    order_by = OrderBy(desc=desc)
    limit_clause = "" if limit is None else "LIMIT $limit"
    q = f"""
        UNWIND $uids AS uid
        MATCH (sent: Sentence {{uid: uid}})
        {q_stats("sent", order_by)}
        RETURN
            sent.uid AS sent_uid
        {(order_by.phrase())}
            , sent.uid ASC
        {limit_clause}
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "uids": [to_uuid(uid).hex for uid in sent_ids],
            "limit": limit,
        },
    )
    return list(flatten(rows))


async def fetch_sort_by_accuracy(
    sent_ids: Iterable[UUID],
    user_id: UUIDy,
    quiz_type: QuizType,
    *,
    limit: int | None = None,
) -> list[UUID]:
    """低正答率、最終回答が古い順に単文を並び替える."""
    if limit is not None and limit < 0:
        msg = "limitは0以上を指定してください"
        raise ValueError(msg)

    limit_clause = "" if limit is None else "LIMIT $limit"
    q = f"""
        UNWIND $uids AS uid
        MATCH (sent: Sentence {{uid: uid}})
        OPTIONAL MATCH (user: User {{uid: $user_id}})
            -[:LEARN]->(quiz: Quiz {{
                quiz_type: $quiz_type,
                is_link_broken: false
            }})-[:QUIZ_TARGET]->(sent)
        OPTIONAL MATCH (user)-[:ANSWER]->(answer: Answer)
            -[:ANSWER_OF]->(quiz)
        WITH
            sent,
            COUNT(answer) AS attempts,
            COUNT(
                CASE WHEN answer.is_correct THEN answer END
            ) AS corrects,
            MAX(answer.created) AS last_attempted_at
        WITH
            sent,
            attempts,
            last_attempted_at,
            CASE
                WHEN attempts = 0 THEN NULL
                ELSE toFloat(corrects) / attempts
            END AS accuracy
        WHERE attempts > 0
        RETURN sent.uid AS sent_uid
        ORDER BY
            accuracy ASC,
            last_attempted_at ASC,
            sent.uid ASC
        {limit_clause}
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "uids": [to_uuid(uid).hex for uid in sent_ids],
            "user_id": to_uuid(user_id).hex,
            "quiz_type": quiz_type.name,
            "limit": limit,
        },
    )
    return list(flatten(rows))


async def fetch_target_ids(  # noqa: PLR0917
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
    pool: QuizTargetPool,
    order: QuizTargetOrder,
    limit: int,
    *,
    exclude_sent_ids: Iterable[UUIDy] | None = None,
    rng: Random | None = None,
) -> list[UUID]:
    """指定した母集団から順序と件数を指定してクイズ対象を取得."""
    if limit < 0:
        msg = "limitは0以上を指定してください"
        raise ValueError(msg)

    match pool:
        case QuizTargetPool.UNCOVERED:
            ids = await fetch_uncovered_sent_ids(resource_id, user_id, quiz_type)
        case QuizTargetPool.COVERED:
            ids = await fetch_covered_sent_ids(resource_id, user_id, quiz_type)

    if exclude_sent_ids is not None:
        excludes = {to_uuid(uid) for uid in exclude_sent_ids}
        ids = [uid for uid in ids if to_uuid(uid) not in excludes]

    match order:
        case QuizTargetOrder.HIGH_SCORE:
            ids = await fetch_sort_by_score(ids, limit=limit)
        case QuizTargetOrder.LOW_SCORE:
            ids = await fetch_sort_by_score(ids, desc=False, limit=limit)
        case QuizTargetOrder.LOW_ACCURACY:
            ids = await fetch_sort_by_accuracy(
                ids,
                user_id,
                quiz_type,
                limit=limit,
            )
        case QuizTargetOrder.RANDOM:
            random = rng if rng is not None else SystemRandom()
            ids = random.sample(ids, k=min(limit, len(ids)))
        case _:
            msg = f"{order}による並び替えは未実装です"
            raise NotImplementedError(msg)

    return ids
