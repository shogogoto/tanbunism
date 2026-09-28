"""Quiz管理domain."""

from uuid import UUID

from pydantic import BaseModel, Field

from tanbun.feature.domain.datetime import Neo4jDateTime
from tanbun.feature.entry.mapper import MResource
from tanbun.feature.quiz.domain.domain import ReadableQuiz
from tanbun.feature.quiz.domain.parts import QuizType


class QuizResourceStatus(BaseModel, frozen=True):
    """Resourceごとの作成済みQuiz状況."""

    resource: MResource
    total_quizzes: int
    quiz_counts: dict[QuizType, int] = Field(default_factory=dict)
    last_created_at: Neo4jDateTime


class SentenceQuizStatus(BaseModel, frozen=True):
    """単文を対象として作成したQuiz状況."""

    sentence_id: UUID
    total_quizzes: int
    quiz_counts: dict[QuizType, int] = Field(default_factory=dict)


class ManagedQuiz(BaseModel, frozen=True):
    """回答状況を含む管理対象Quiz."""

    quiz: ReadableQuiz
    attempts: int
    corrects: int
    accuracy: float | None
    last_attempted_at: Neo4jDateTime | None


class ManagedQuizResult(BaseModel, frozen=True):
    """管理対象Quizのページング結果."""

    data: list[ManagedQuiz]
    total: int


class BrokenQuizReference(BaseModel, frozen=True):
    """作成Quizから退役単文へ残された、修復可能な参照."""

    quiz_id: UUID
    retired_sentence_id: UUID
    retired_value: str
    resource_id: UUID
    roles: list[str]
    retired_at: Neo4jDateTime


class QuizReattachmentResult(BaseModel, frozen=True):
    """1件のQuizで付け替えた関係数と退役単文の保持状態."""

    quiz_targets: int
    quiz_options: int
    quiz_corrects: int
    retained: bool
