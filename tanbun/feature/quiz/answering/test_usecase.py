"""クイズ回答usecase test."""

import pytest
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.answering.usecase import answer_quiz_as_chain
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.chain.errors import QuizChainNotFoundError
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aregister

u = async_fixture()(fx_u)


@mark_async_test()
async def test_answer_quiz_as_chain(u: LUser):
    """回答結果と復習に必要な1ホップをまとめて返す."""
    target = await LSentence.nodes.first(val="ccc")
    quiz = await generate_quiz(
        QuizType.SENT2TERM,
        CandidateType.NEAR,
        target.uid,
        4,
        u.uid,
    )
    readable = quiz.to_readable()

    chain = await answer_quiz_as_chain(quiz.quiz_id, readable.correct, u.uid)

    assert chain.answers[0].is_correct
    assert chain.answers[0].selected == readable.correct
    assert [item.quiz_id for item in chain.quizzes] == [quiz.quiz_id]
    rows, _ = await adb.cypher_query(
        "MATCH (a:Answer {uid:$uid}) RETURN a.source",
        {"uid": chain.answers[0].answer_uid.hex},
    )
    assert rows[0][0] == "review"


@mark_async_test()
async def test_reject_answering_unassigned_quiz(u: LUser):
    """学習対象ではない他ユーザーのQuizには回答できない."""
    target = await LSentence.nodes.first(val="ccc")
    other = await aregister(email="quiz-answer-chain-other@ex.com")
    quiz = await generate_quiz(
        QuizType.SENT2TERM,
        CandidateType.NEAR,
        target.uid,
        4,
        other.uid,
    )
    readable = quiz.to_readable()

    with pytest.raises(QuizChainNotFoundError):
        await answer_quiz_as_chain(quiz.quiz_id, readable.correct, u.uid)
