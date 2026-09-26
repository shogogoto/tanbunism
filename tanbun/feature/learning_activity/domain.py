"""学習活動の集計モデル."""

from pydantic import BaseModel


class LearningActivityCounts(BaseModel, frozen=True):
    """ユーザーが積み上げた学習活動の総量."""

    n_sentence: int = 0
    n_quiz_created: int = 0
    n_quiz_answered: int = 0
    n_quiz_correct: int = 0
