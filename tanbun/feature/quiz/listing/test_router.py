"""クイズ・回答一覧APIのテスト."""

from httpx import AsyncClient
from starlette import status

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
async def test_list_quiz_feed_api(ac: AsyncClient, u: LUser):
    """全ユーザーQuiz TLを回答状況付きで取得."""
    other = await LUser(email="quiz-feed-api-other@ex.com").save()
    own = await _generate_quizzes(u, 1)
    another = await _generate_quizzes(other, 1)

    response = await ac.get(
        "/quiz/feed",
        headers=await aauth_header(email=u.email),
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["total"] == len(own) + len(another)
    assert {item["quiz"]["quiz_id"] for item in response.json()["data"]} == {
        str(own[0].quiz_id),
        str(another[0].quiz_id),
    }


@mark_async_test()
async def test_daily_quizzes_scope_and_progress(ac: AsyncClient, u: LUser):
    """個人と全体を分離し、回答後の再取得でも同じ推薦セットを維持する."""
    own = await _generate_quizzes(u, 3)
    other = await LUser(email="daily-api-other@ex.com").save()
    await _generate_quizzes(other, 1)
    headers = await aauth_header(email=u.email)
    response = await ac.get("/quiz/daily", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    items = response.json()["data"]
    expected = [item["quiz"]["quiz_id"] for item in items]
    assert set(expected) == {str(quiz.quiz_id) for quiz in own}
    assert not any(item["answered_today"] for item in items)
    selected = next(quiz for quiz in own if str(quiz.quiz_id) == expected[0])
    await create_answer(selected.quiz_id, selected.to_readable().correct, u.uid)
    refreshed = await ac.get("/quiz/daily", headers=headers)
    assert [item["quiz"]["quiz_id"] for item in refreshed.json()["data"]] == expected
    assert refreshed.json()["data"][0]["answered_today"]
    global_response = await ac.get("/quiz/daily?personal=false", headers=headers)
    assert global_response.json()["total"] == len(own) + 1


@mark_async_test()
async def test_daily_quizzes_excludes_newly_broken_quiz(ac: AsyncClient, u: LUser):
    """同日のセットに含まれていても、参照切れになったQuizは除外する."""
    from neomodel import adb  # noqa: PLC0415

    await _generate_quizzes(u, 2)
    headers = await aauth_header(email=u.email)
    first = await ac.get("/quiz/daily", headers=headers)
    removed = first.json()["data"][0]["quiz"]["quiz_id"].replace("-", "")
    await adb.cypher_query(
        """
        MATCH (quiz:Quiz {uid: $uid})
        CREATE (quiz)-[:BROKEN_BY]->(:RetiredSentence {uid: 'retired-daily'})
        """,
        params={"uid": removed},
    )
    refreshed = await ac.get("/quiz/daily", headers=headers)
    assert refreshed.status_code == status.HTTP_200_OK
    assert refreshed.json()["total"] == 1


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
