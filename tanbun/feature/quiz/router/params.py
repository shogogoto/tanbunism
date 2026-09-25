"""quiz router param."""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.limits import MAX_OPTIONS_PER_QUIZ, MAX_QUIZZES_PER_REQUEST


class BatchCreateQuizParam(BaseModel, frozen=True):
    """リソース単位で一括クイズ作成."""

    resource_uid: str
    n_quiz: int = Field(ge=0, le=MAX_QUIZZES_PER_REQUEST)
    quiz_type: QuizType
    cand_type: CandidateType
    n_option: int = Field(default=4, ge=1, le=MAX_OPTIONS_PER_QUIZ)
    allow_multiple_anwser: bool = False
    allow_no_correct_option: bool = False


class CreateQuizParam(BaseModel, frozen=True):
    """指定単文からクイズ作成."""

    target_sent_uid: str
    quiz_type: QuizType
    cand_type: CandidateType
    n_option: int = Field(default=4, ge=1, le=MAX_OPTIONS_PER_QUIZ)
    correct_sent_uids: list[str] = Field(default_factory=list)
    allow_multiple_anwser: bool = False
    allow_no_correct_option: bool = False

    @model_validator(mode="after")
    def relation_quiz_requires_correct_sentences(self) -> Self:
        """関係クイズには正解となる関係先が必要."""
        if not self.quiz_type.has_term and not self.correct_sent_uids:
            msg = "関係クイズには正解となる関係先の単文を指定してください"
            raise ValueError(msg)
        return self


# create_quizの引数そのまま
# 欲しくなったら実装
# class CreateQuizManuallyParam(BaseModel, frozen=True):
#     """選択肢などすべてユーザーが選ぶ."""


class AnswerParam(BaseModel, frozen=True):
    """回答パラメータ."""

    selected: list[str]


class AnswerFeedback(BaseModel, frozen=True):
    """回答フィードバック."""

    is_correct: bool
