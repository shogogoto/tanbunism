"""クイズ生成repo."""

from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID, uuid4

from neomodel import adb

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.distractor.distractor import (
    fetch_distractor_ids,
    fetch_pair2rel_distractor_ids,
)
from tanbun.feature.quiz.domain.domain import (
    QuizSource,
    QuizType,
)
from tanbun.feature.quiz.eligibility import filter_defined_sentence_ids
from tanbun.feature.quiz.errors import InsufficientOptionsError
from tanbun.feature.quiz.repo.restore import restore_quiz_sources


async def create_quiz_and_correct(  # noqa: PLR0917
    target_sent_uid: UUIDy,
    quiz_type: QuizType,
    option_uids: Sequence[UUIDy],
    user_uid: UUIDy,  # 作成者兼、現在の学習者
    correct_uids: Sequence[UUIDy] | None = None,
    no_correct_option: bool = False,  # noqa: FBT001, FBT002
) -> UUID:
    """クイズとその正解の永続化."""
    now = datetime.now(tz=TZ)
    quiz_uid = uuid4()
    if correct_uids is None:
        correct_uids = []
    q = """
        MATCH (tgt: Sentence {uid: $target_uid})
        CREATE (quiz: Quiz {
            uid: $quiz_uid
            , quiz_type: $quiz_type
            , is_link_broken: false
            , no_correct_option: $no_correct_option
            , created: datetime($now)
        })-[:QUIZ_TARGET]->(tgt)
        WITH quiz
        CALL (quiz) {
            OPTIONAL MATCH (u: User {uid: $user_uid})
            WITH quiz, u WHERE u IS NOT NULL
            CREATE
                (u)-[:CREATE]->(quiz),
                (u)-[:LEARN]->(quiz)
        }
        WITH quiz
        UNWIND $option_uids AS ouid
        MATCH (opt: Sentence {uid: ouid})
        CREATE (quiz)-[:QUIZ_OPTION]->(opt)
        WITH DISTINCT quiz
        UNWIND  $correct_uids AS cuid
        MATCH (c: Sentence {uid: cuid})
        CREATE (quiz)-[:CORRECT]->(c)
    """
    _, _ = await adb.cypher_query(
        q,
        params={
            "quiz_uid": quiz_uid.hex,
            "target_uid": to_uuid(target_sent_uid).hex,
            "option_uids": [to_uuid(u).hex for u in option_uids],
            "correct_uids": [to_uuid(u).hex for u in correct_uids],
            "quiz_type": quiz_type.name,
            "user_uid": to_uuid(user_uid).hex if user_uid is not None else None,
            "now": now.isoformat(),
            "no_correct_option": no_correct_option,
        },
    )
    return quiz_uid


async def check_duplicate_for_precreate(
    sent_id: str,
    qt: QuizType,
    option_ids: Sequence[str],
    correct_sent_uids: list[str] | None = None,
) -> bool:
    """同じ構成のクイズが既存か."""
    if correct_sent_uids is None:
        correct_sent_uids = []
    q = """
        MATCH (s: Sentence {uid: $sent_uid})
        MATCH(quiz: Quiz)-[:QUIZ_TARGET]->(s)
        RETURN quiz.uid
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "sent_uid": to_uuid(sent_id).hex,
        },
    )

    def eq_uuidy(id1: UUIDy, id2: UUIDy) -> bool:
        return to_uuid(id1) == to_uuid(id2)

    def eq_uuidys(ids1: Iterable[UUIDy], ids2: Iterable[UUIDy]) -> bool:
        return {to_uuid(id1) for id1 in ids1} == {to_uuid(id2) for id2 in ids2}

    if not rows:
        return False

    srcs = await restore_quiz_sources(rows[0])
    for s in srcs:
        eq_t = s.quiz_type == qt
        eq_tgt = eq_uuidy(s.target_id, sent_id)
        eq_opt = eq_uuidys(s.sources.keys(), option_ids)
        eq_crct = eq_uuidys(s.correct_ids, correct_sent_uids)
        if eq_t and eq_tgt and eq_opt and eq_crct:
            return True
    return False


async def prepare_quiz_gen(  # noqa: PLR0917
    qt: QuizType,
    ct: CandidateType,
    target_sent_uid: UUIDy,
    n_option: int,
    correct_sent_uids: list[UUIDy] | None = None,
    without_correct_option: bool = False,  # noqa: FBT001, FBT002
) -> tuple[list[UUID], list[UUIDy]]:
    """クイズ生成用に単文idのセットを返す."""
    required_ids = [target_sent_uid, *(correct_sent_uids or [])]
    defined_ids = await filter_defined_sentence_ids(required_ids)
    if {to_uuid(uid) for uid in required_ids} != set(defined_ids):
        msg = "説明のない用語はクイズの対象または正解にできません"
        raise InsufficientOptionsError(msg)

    correct_ids = qt.correct_ids(target_sent_uid, correct_sent_uids)
    n_ds = n_option - len(correct_ids)
    if without_correct_option:
        n_ds = n_option
    if qt is QuizType.PAIR2REL:
        ds = await fetch_pair2rel_distractor_ids(
            target_sent_uid,
            ct,
            n_ds,
            correct_ids,
        )
    else:
        ds = await fetch_distractor_ids(
            [target_sent_uid],
            ct,
            n_ds,
            qt.has_term,
            correct_ids,
        )
    return ds, correct_ids


async def generate_quiz(  # noqa: PLR0917
    qt: QuizType,
    ct: CandidateType,
    target_sent_uid: UUIDy,
    n_option: int,
    user_id: UUIDy,
    correct_sent_uids: list[UUIDy] | None = None,
    no_correct_option: bool = False,  # noqa: FBT001, FBT002
) -> QuizSource:
    """高級なクイズ生成."""
    ds, correct_ids = await prepare_quiz_gen(
        qt,
        ct,
        target_sent_uid,
        n_option,
        correct_sent_uids,
        no_correct_option,
    )
    quiz_id = await create_quiz_and_correct(
        target_sent_uid,
        qt,
        ds,
        user_uid=user_id,
        correct_uids=correct_ids,
        no_correct_option=no_correct_option,
    )
    srcs = await restore_quiz_sources([quiz_id])
    return srcs[0]
