"""保存可能な学習計画."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.limits import (
    MAX_OPTIONS_PER_QUIZ,
    MAX_QUIZZES_PER_REQUEST,
    MAX_RESOURCES_PER_STUDY_PLAN,
)


class StudyPlanDraft(BaseModel, frozen=True):
    """StudyPlan作成時の設定."""

    name: str = Field(min_length=1)
    resource_ids: list[UUID] = Field(
        min_length=1,
        max_length=MAX_RESOURCES_PER_STUDY_PLAN,
    )
    quiz_types: list[QuizType] = Field(min_length=1)
    n_quiz: int = Field(ge=0, le=MAX_QUIZZES_PER_REQUEST)
    n_option: int = Field(ge=1, le=MAX_OPTIONS_PER_QUIZ)

    @field_validator("resource_ids", "quiz_types")
    @classmethod
    def unique_items(cls, items: list) -> list:
        """入力の優先順を維持して重複を除く."""
        return list(dict.fromkeys(items))


class StudyPlan(StudyPlanDraft, frozen=True):
    """永続化された学習計画."""

    uid: UUID
    created: datetime
