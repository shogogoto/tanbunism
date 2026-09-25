"""複数リソースを横断するクイズ推薦のテスト."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.fixture import (
    create_learning_test_resource,
    fx_learning,
    generate_test_quizzes,
    learning_resource_id,
)
from tanbun.feature.quiz.learning.recommendation import (
    usecase as recommendation_usecase,
)
from tanbun.feature.quiz.learning.recommendation.domain import (
    QuizRecommendationReason,
)
from tanbun.feature.quiz.learning.recommendation.usecase import (
    recommend_quizzes,
)
from tanbun.feature.user.label import LUser

u = async_fixture()(fx_learning)


@mark_async_test()
async def test_skip_resources_allocated_zero_quizzes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """割当が0件のResourceにはDBアクセスや生成処理を行わない."""
    prepare = AsyncMock(return_value=[])
    monkeypatch.setattr(
        recommendation_usecase,
        "_prepare_resource_quizzes",
        prepare,
    )
    first = uuid4()
    second = uuid4()

    await recommend_quizzes(
        [first, second],
        uuid4(),
        QuizType.TERM2SENT,
        CandidateType.ALL,
        n_quiz=1,
        n_option=3,
    )

    prepare.assert_awaited_once()
    assert prepare.await_args.args[0] == first


@mark_async_test()
async def test_recommend_quizzes_round_robin(u: LUser):
    """入力順を優先して複数リソースから均等に推薦."""
    first = await learning_resource_id(u.uid)
    second = await create_learning_test_resource(u.uid)
    n_quiz = 3

    recommendations = await recommend_quizzes(
        [first, second],
        u.uid,
        QuizType.TERM2SENT,
        CandidateType.ALL,
        n_quiz=n_quiz,
        n_option=3,
    )

    assert [item.resource_id for item in recommendations] == [
        first,
        second,
        first,
    ]
    assert len({item.quiz.quiz_id for item in recommendations}) == n_quiz


@mark_async_test()
async def test_recommend_quizzes_only_from_selected_resources(u: LUser):
    """提案対象として渡していないリソースは推薦しない."""
    await learning_resource_id(u.uid)
    selected = await create_learning_test_resource(u.uid)
    n_quiz = 2

    recommendations = await recommend_quizzes(
        [selected],
        u.uid,
        QuizType.TERM2SENT,
        CandidateType.ALL,
        n_quiz=n_quiz,
        n_option=3,
    )

    assert len(recommendations) == n_quiz
    assert {item.resource_id for item in recommendations} == {selected}


@mark_async_test()
async def test_recommend_existing_unattempted_quiz_first(u: LUser):
    """新規生成より既存の未回答クイズを優先."""
    resource_id = await learning_resource_id(u.uid)
    existing = (await generate_test_quizzes(resource_id, u.uid, 1))[0]

    recommendations = await recommend_quizzes(
        [resource_id],
        u.uid,
        QuizType.TERM2SENT,
        CandidateType.ALL,
        n_quiz=1,
        n_option=3,
    )

    assert recommendations[0].quiz.quiz_id == existing.quiz_id
    assert recommendations[0].reason is QuizRecommendationReason.UNATTEMPTED


@mark_async_test()
async def test_recommend_without_generating_missing_quizzes(u: LUser):
    """既存取得段階では、不足していても新しいQuizを作らない."""
    resource_id = await learning_resource_id(u.uid)

    recommendations = await recommend_quizzes(
        [resource_id],
        u.uid,
        QuizType.TERM2SENT,
        CandidateType.ALL,
        n_quiz=1,
        n_option=3,
        generate_missing=False,
    )

    assert recommendations == []


@mark_async_test()
async def test_relation_quiz_is_generated_from_knowledge_path(u: LUser):
    """既存Quizがなければknowledge pathから関係Quizを生成する."""
    resource_id = await learning_resource_id(u.uid)

    recommendations = await recommend_quizzes(
        [resource_id],
        u.uid,
        QuizType.REL2PAIR,
        CandidateType.ALL,
        n_quiz=1,
        n_option=3,
    )

    assert len(recommendations) == 1
    assert recommendations[0].quiz.quiz_type is QuizType.REL2PAIR
