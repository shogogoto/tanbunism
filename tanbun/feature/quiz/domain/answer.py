"""回答ドメイン."""

from uuid import UUID

from pydantic import BaseModel, RootModel

from tanbun.feature.domain.datetime import Neo4jDateTime
from tanbun.feature.quiz.domain.domain import ReadableQuiz
from tanbun.feature.quiz.domain.parts import QuizType


class Answer(BaseModel, frozen=True):
    """誰がいつ何を選択して回答したか、とその正誤."""

    answer_uid: UUID
    quiz_uid: UUID
    selected: list[str]  # 複数選択可
    who: UUID
    is_correct: bool
    created: Neo4jDateTime


class Answers(RootModel[list[Answer]]):
    """回答一覧."""


class QuizAnswers(BaseModel, frozen=True):
    """クイズに対する回答集."""

    rq: ReadableQuiz
    answers: list[Answer]


class AnswerHistoryItem(BaseModel, frozen=True):
    """回答履歴一覧の1件."""

    answer: Answer
    quiz_type: QuizType
    resource_id: UUID
    quiz: ReadableQuiz


class AnswerHistoryResult(BaseModel, frozen=True):
    """ページングされた回答履歴."""

    data: list[AnswerHistoryItem]
    total: int
