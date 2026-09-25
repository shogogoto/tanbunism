"""クイズ・回答一覧APIのテスト."""

from httpx import AsyncClient

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.answering.repo import create_answer
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.answer import AnswerHistoryResult, Answers
from tanbun.feature.quiz.domain.collections import ReadableQuizResult
from tanbun.feature.quiz.domain.domain import QuizSource
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aauth_header

u = async_fixture()(fx_u)


async def _generate_quizzes(u: LUser, count: int) -> list[QuizSource]:
    """一覧API用のクイズを生成."""
    target = await LSentence.nodes.first(val="ccc")
    return [
        await generate_quiz(
            QuizType.TERM2SENT,
            CandidateType.ALL,
            target.uid,
            3,
            u.uid,
        )
        for _ in range(count)
    ]


@mark_async_test()
async def test_list_learning_quizzes_api(ac: AsyncClient, u: LUser):
    """学習対象クイズをページングして取得."""
    quizzes = await _generate_quizzes(u, 3)
    headers = await aauth_header(email=u.email)
    page_size = 2

    response = await ac.get(
        "/quiz",
        params={"page": 1, "size": page_size},
        headers=headers,
    )
    result = ReadableQuizResult.model_validate(response.json())

    assert result.total == len(quizzes)
    assert len(result.data.root) == page_size


@mark_async_test()
async def test_list_own_answers_api(ac: AsyncClient, u: LUser):
    """指定クイズに対する認証ユーザー自身の回答を取得."""
    quiz = (await _generate_quizzes(u, 1))[0].to_readable()
    correct = await create_answer(quiz.quiz_id, quiz.correct, u.uid)
    wrong = await create_answer(quiz.quiz_id, [quiz.distractors[0]], u.uid)

    response = await ac.get(
        f"/quiz/answer/{quiz.quiz_id}",
        headers=await aauth_header(email=u.email),
    )
    answers = Answers.model_validate(response.json())

    assert {answer.answer_uid for answer in answers.root} == {
        correct.answer_uid,
        wrong.answer_uid,
    }


@mark_async_test()
async def test_list_answer_history_api(ac: AsyncClient, u: LUser):
    """回答履歴を正誤で絞り込み、Quiz本文とともに取得."""
    quizzes = [quiz.to_readable() for quiz in await _generate_quizzes(u, 2)]
    correct = await create_answer(quizzes[0].quiz_id, quizzes[0].correct, u.uid)
    wrong = await create_answer(
        quizzes[1].quiz_id,
        [quizzes[1].distractors[0]],
        u.uid,
    )

    response = await ac.get(
        "/quiz/answers",
        params={"is_correct": False, "page": 1, "size": 1},
        headers=await aauth_header(email=u.email),
    )
    history = AnswerHistoryResult.model_validate(response.json())

    assert history.total == 1
    assert len(history.data) == 1
    assert history.data[0].answer.answer_uid == wrong.answer_uid
    assert history.data[0].answer.answer_uid != correct.answer_uid
    assert history.data[0].quiz.statement
    assert history.data[0].quiz_type == QuizType.TERM2SENT
