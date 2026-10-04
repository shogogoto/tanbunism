"""クイズ一覧取得のテスト."""

from uuid import UUID, uuid4

from pytest_unordered import unordered

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.types import UUIDy
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.distractor.distractor import fetch_distractor_ids
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.generation.repo import create_quiz_and_correct
from tanbun.feature.quiz.learning.assignment.repo import (
    assign_quiz_to_learner,
)
from tanbun.feature.quiz.listing.repo import (
    list_learning_quizzes,
    list_quiz_by_sentence_ids,
    list_quiz_by_user_ids,
    list_quiz_feed,
)
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser

u = async_fixture()(fx_u)

QUIZ_TYPES = [
    QuizType.TERM2SENT,
    QuizType.TERM2SENT,
    QuizType.SENT2TERM,
]


async def _create_quiz_set(user_id: UUIDy, target: str) -> list[UUID]:
    """一覧テスト用に3件のクイズを作成."""
    qids = []
    sentence = await LSentence.nodes.first(val=target)
    option_ids = await fetch_distractor_ids(
        [sentence.uid],
        CandidateType.NEAR,
        3,
        True,  # noqa: FBT003
    )
    for quiz_type in QUIZ_TYPES:
        qid = await create_quiz_and_correct(
            sentence.uid,
            quiz_type,
            option_ids,
            user_uid=user_id,
        )
        qids.append(qid)
    return qids


@mark_async_test()
async def test_list_quiz_separated(u: LUser):
    """指定していないユーザーのクイズは取得しない."""
    other = await LUser(email="quiz2@ex.com").save()
    expected_ids = await _create_quiz_set(u.uid, "ccc")
    await _create_quiz_set(other.uid, "ccc")

    result = await list_quiz_by_user_ids([u.uid])

    assert [quiz.quiz_id for quiz in result.data.root] == unordered(expected_ids)


@mark_async_test()
async def test_list_learning_quizzes(u: LUser):
    """CREATEに関係なくLEARNがあるクイズを取得."""
    other = await LUser(email="quiz2@ex.com").save()
    own_ids = await _create_quiz_set(u.uid, "ccc")
    other_ids = await _create_quiz_set(other.uid, "ccc")
    assigned_id = other_ids[0]
    await assign_quiz_to_learner(assigned_id, u.uid)

    result = await list_learning_quizzes(u.uid)

    assert result.total == len(own_ids) + 1
    assert [quiz.quiz_id for quiz in result.data.root] == unordered(
        [*own_ids, assigned_id],
    )


@mark_async_test()
async def test_list_quiz_feed_includes_other_users_quizzes(u: LUser):
    """全ユーザーTLには他ユーザーが作成したQuizも含める."""
    other = await LUser(email="quiz-feed-other@ex.com").save()
    own_ids = await _create_quiz_set(u.uid, "ccc")
    other_ids = await _create_quiz_set(other.uid, "ccc")

    result = await list_quiz_feed(u.uid)

    assert result.total == len(own_ids) + len(other_ids)
    assert [item.quiz.quiz_id for item in result.data] == unordered(
        [*own_ids, *other_ids],
    )
    assert all(item.attempts == 0 for item in result.data)


@mark_async_test()
async def test_list_quiz_by_sentence_id(u: LUser):
    """単文指定でクイズを取得."""
    sent = await LSentence.nodes.first(val="ccc")
    expected_ids = await _create_quiz_set(u.uid, "ccc")
    await _create_quiz_set(u.uid, "ccc1")

    result = await list_quiz_by_sentence_ids([sent.uid])

    assert [quiz.quiz_id for quiz in result.data.root] == unordered(expected_ids)


@mark_async_test()
async def test_list_quiz_by_sentence_id_empty(u: LUser):
    """クイズが存在しない単文を指定した場合、空のリストが返る."""
    sent = await LSentence.nodes.first(val="ccc")
    result = await list_quiz_by_sentence_ids([sent.uid])
    assert result.data.root == []


@mark_async_test()
async def test_list_quiz_unexist_sentence(u: LUser):
    """存在しない単文IDを指定した場合、空のリストが返る."""
    result = await list_quiz_by_sentence_ids([uuid4()])
    assert result.data.root == []
