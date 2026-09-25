"""StudyPlanドメインのテスト."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.study_plan.domain import StudyPlanDraft
from tanbun.feature.quiz.limits import (
    MAX_OPTIONS_PER_QUIZ,
    MAX_QUIZZES_PER_REQUEST,
    MAX_RESOURCES_PER_STUDY_PLAN,
)


def draft_values() -> dict:
    """有効なStudyPlan入力を返す."""
    return {
        "name": "plan",
        "resource_ids": [uuid4()],
        "quiz_types": [QuizType.TERM2SENT],
        "n_quiz": 10,
        "n_option": 4,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "resource_ids",
            [uuid4() for _ in range(MAX_RESOURCES_PER_STUDY_PLAN + 1)],
        ),
        ("n_quiz", MAX_QUIZZES_PER_REQUEST + 1),
        ("n_option", MAX_OPTIONS_PER_QUIZ + 1),
    ],
)
def test_reject_synchronous_workload_over_limit(field: str, value: object) -> None:
    """同期リクエストの処理上限を超えるStudyPlanを拒否する."""
    values = draft_values()
    values[field] = value

    with pytest.raises(ValidationError):
        StudyPlanDraft.model_validate(values)
