"""クイズ回答後の報酬処理はAPIで接続し、機能間の逆依存を作らない."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tanbun.api import _grant_quiz_review_reward as grant_quiz_reward
from tanbun.api import api
from tanbun.feature.quiz.chain.domain import QuizChain
from tanbun.feature.quiz.router import router as quiz
from tanbun.feature.quiz.router.params import AnswerParam


@pytest.fixture(autouse=True)
def setup():
    """Keep callback tests independent of the database."""


@pytest.mark.asyncio
async def test_review_reward_is_called_after_answer_and_returned(monkeypatch):
    """回答の記録前に判定せず、付与結果を既存レスポンスへ含める."""
    quiz_id, user_id = uuid4(), uuid4()
    recorded = False

    async def answer(*_args, **_kwargs):  # noqa: RUF029
        nonlocal recorded
        recorded = True
        return QuizChain(sentences=[], quizzes=[], links=[])

    async def reward(uid):  # noqa: RUF029
        assert recorded
        assert uid == user_id
        return True

    monkeypatch.setattr(quiz, "answer_quiz_as_chain", answer)
    result = await quiz.answer_quiz_api(
        quiz_id,
        AnswerParam(selected=["yes"]),
        SimpleNamespace(uid=user_id),
        reward,
    )
    assert result.step_recovery_awarded


@pytest.mark.asyncio
async def test_api_connects_quiz_reward_to_review_completion(monkeypatch):
    """APIの依存注入がゲーム側の復習20問報酬へつながっている."""
    award = AsyncMock(return_value=(None, True))
    monkeypatch.setattr("tanbun.api.grant_review_reward", award)
    handler = api.dependency_overrides[quiz.review_reward_handler]()
    assert handler is grant_quiz_reward
    user_id = uuid4()
    assert await handler(user_id)
    award.assert_awaited_once_with(user_id, quiz=True)
