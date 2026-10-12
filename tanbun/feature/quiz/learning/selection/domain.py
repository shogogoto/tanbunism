"""学習用クイズの対象選択."""

from enum import StrEnum, auto


class QuizTargetPool(StrEnum):
    """クイズ対象を選ぶ母集団."""

    UNCOVERED = auto()
    COVERED = auto()


class QuizTargetOrder(StrEnum):
    """クイズ生成用の単文の順番."""

    HIGH_SCORE = auto()
    LOW_SCORE = auto()
    LOW_ACCURACY = auto()
    RANDOM = auto()
    PAGERANK = auto()


class QuizFillStrategy(StrEnum):
    """クイズ生成方式."""

    COVERAGE = auto()
    REVIEW = auto()
    IMPORTANCE = auto()
    PAGERANK = auto()

    @property
    def target_selection(self) -> tuple[QuizTargetPool, QuizTargetOrder]:
        """クイズ対象の母集団と並び順."""
        return {
            QuizFillStrategy.PAGERANK: (
                QuizTargetPool.UNCOVERED,
                QuizTargetOrder.PAGERANK,
            ),
            QuizFillStrategy.COVERAGE: (
                QuizTargetPool.UNCOVERED,
                QuizTargetOrder.RANDOM,
            ),
            QuizFillStrategy.REVIEW: (
                QuizTargetPool.COVERED,
                QuizTargetOrder.LOW_ACCURACY,
            ),
            QuizFillStrategy.IMPORTANCE: (
                QuizTargetPool.UNCOVERED,
                QuizTargetOrder.HIGH_SCORE,
            ),
        }[self]
