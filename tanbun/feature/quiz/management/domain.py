"""Quiz管理domain."""

from enum import StrEnum
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
    answered_today: bool = False


class ManagedQuizResult(BaseModel, frozen=True):
    """管理対象Quizのページング結果."""

    data: list[ManagedQuiz]
    total: int


class DeleteQuizzesResult(BaseModel, frozen=True):
    """作成済みQuizの一括削除結果."""

    deleted_count: int
    deleted_answer_count: int
    skipped_count: int


class BrokenQuizReference(BaseModel, frozen=True):
    """作成Quizから退役単文へ残された、修復可能な参照."""

    quiz_id: UUID
    quiz_type: QuizType
    retired_sentence_id: UUID
    retired_value: str
    resource_id: UUID
    resource_name: str | None
    roles: list[str]
    retired_at: Neo4jDateTime


class UnplannedQuiz(BaseModel, frozen=True):
    """所有StudyPlanの対象範囲に含まれない作成済みQuiz."""

    quiz_id: UUID
    quiz_type: QuizType
    resource_id: UUID
    resource_name: str | None


class QuizIssueSummary(BaseModel, frozen=True):
    """作成者が確認すべきQuizと整理候補の件数."""

    broken_count: int
    reported_count: int
    unplanned_count: int
    total_count: int


class QuizReattachmentResult(BaseModel, frozen=True):
    """1件のQuizで付け替えた関係数と退役単文の保持状態."""

    quiz_targets: int
    quiz_options: int
    quiz_corrects: int
    retained: bool


class QuizReportReason(StrEnum):
    """Quizの不備分類."""

    UNDEFINED = "undefined"
    INCORRECT = "incorrect"
    OTHER = "other"


class QuizReportRequest(BaseModel, frozen=True):
    """Quiz不備の報告内容."""

    reason: QuizReportReason
    detail: str | None = Field(default=None, max_length=500)


class QuizReport(BaseModel, frozen=True):
    """作成者が確認するQuiz不備報告."""

    quiz_id: UUID
    quiz: ReadableQuiz
    reason: QuizReportReason
    detail: str | None
    report_count: int
    resource_id: UUID | None
    resource_name: str | None
    updated_at: Neo4jDateTime
