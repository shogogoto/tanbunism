"""StudyPlanのユースケーステスト."""

from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.datetime import TZ
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.fixture import (
    create_learning_test_resource,
    fx_learning,
    learning_resource_id,
)
from tanbun.feature.quiz.learning.study_plan.domain import StudyPlan, StudyPlanDraft
from tanbun.feature.quiz.learning.study_plan.errors import (
    StudyPlanNotFoundError,
)
from tanbun.feature.quiz.learning.study_plan.repo import create_study_plan
from tanbun.feature.quiz.learning.study_plan.usecase import (
    prepare_additional_study_plan_quizzes,
    recommend_quizzes_for_study_plan,
)
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aregister

u = async_fixture()(fx_learning)
MIN_RECOMMENDED_OPTIONS = 2


@mark_async_test()
async def test_prepare_redistributes_quizzes_from_unavailable_types(
    monkeypatch: pytest.MonkeyPatch,
):
    """生成不能な形式の割当分を生成可能な形式で補う."""
    requested_count = 5
    plan = StudyPlan(
        uid=uuid4(),
        name="形式を補完",
        resource_ids=[uuid4()],
        quiz_types=[QuizType.TERM2SENT, QuizType.SENT2TERM],
        n_quiz=requested_count,
        n_option=4,
        created=datetime.now(tz=TZ),
    )
    generated = 0

    def generate(*args, **kwargs):
        nonlocal generated
        if args[2] is QuizType.TERM2SENT:
            return []
        quizzes = [object()] * kwargs["n_quiz"]
        generated += len(quizzes)
        return quizzes

    count = AsyncMock(side_effect=[0, requested_count])
    monkeypatch.setattr(
        "tanbun.feature.quiz.learning.study_plan.usecase.get_study_plan",
        AsyncMock(return_value=plan),
    )
    monkeypatch.setattr(
        "tanbun.feature.quiz.learning.study_plan.usecase.count_prepared_quizzes",
        count,
    )
    monkeypatch.setattr(
        "tanbun.feature.quiz.learning.study_plan.usecase.generate_quizzes",
        AsyncMock(side_effect=generate),
    )

    result = await prepare_additional_study_plan_quizzes(
        plan.uid,
        uuid4(),
        requested_count,
        send_notification=False,
    )

    assert generated == requested_count
    assert result.added_count == requested_count
    assert result.prepared_quiz_count == requested_count


@mark_async_test()
async def test_recommend_quizzes_for_study_plan(u: LUser):
    """保存したresource順と設定でクイズを推薦."""
    first = await learning_resource_id(u.uid)
    second = await create_learning_test_resource(u.uid)
    plan = await create_study_plan(
        u.uid,
        StudyPlanDraft(
            name="毎日の学習",
            resource_ids=[first, second],
            quiz_types=[QuizType.TERM2SENT, QuizType.SENT2TERM],
            n_quiz=3,
            n_option=3,
        ),
    )

    recommendations = await recommend_quizzes_for_study_plan(
        plan.uid,
        u.uid,
    )

    assert [item.resource_id for item in recommendations] == [
        first,
        first,
        second,
    ]
    assert len({item.quiz.quiz_id for item in recommendations}) == plan.n_quiz
    assert {item.quiz.quiz_type for item in recommendations} == {
        QuizType.TERM2SENT,
        QuizType.SENT2TERM,
    }

    other = await aregister(email="study-plan-reader@ex.com")
    with pytest.raises(StudyPlanNotFoundError):
        await recommend_quizzes_for_study_plan(plan.uid, other.uid)


@mark_async_test()
async def test_recommend_quizzes_across_all_quiz_types(u: LUser):
    """自動生成できるQuizTypeを、関係QuizTypeと同時選択しても推薦する."""
    resource_id = await learning_resource_id(u.uid)
    plan = await create_study_plan(
        u.uid,
        StudyPlanDraft(
            name="全形式",
            resource_ids=[resource_id],
            quiz_types=[
                QuizType.TERM2SENT,
                QuizType.SENT2TERM,
                QuizType.REL2PAIR,
                QuizType.PAIR2REL,
            ],
            n_quiz=1,
            n_option=4,
        ),
    )

    recommendations = await recommend_quizzes_for_study_plan(plan.uid, u.uid)

    assert [item.quiz.quiz_type for item in recommendations] == [
        QuizType.TERM2SENT,
        QuizType.SENT2TERM,
        QuizType.REL2PAIR,
        QuizType.PAIR2REL,
    ]
    pair2rel = recommendations[-1].quiz.to_readable()
    assert MIN_RECOMMENDED_OPTIONS <= len(pair2rel.options) <= plan.n_option

    pair_recommendations = await recommend_quizzes_for_study_plan(
        plan.uid,
        u.uid,
        QuizType.PAIR2REL,
    )
    assert {item.quiz.quiz_type for item in pair_recommendations} == {
        QuizType.PAIR2REL,
    }
