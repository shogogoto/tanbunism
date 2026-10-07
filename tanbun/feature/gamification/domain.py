"""経験値とレベルのモデル・規則."""

from enum import StrEnum
from math import isqrt

from pydantic import BaseModel

from tanbun.feature.learning_activity.domain import LearningActivityCounts

TANBUN_EXPOSURE_XP = 1
QUIZ_ANSWERED_XP = 5
QUIZ_CORRECT_BONUS_XP = 2
DEFAULT_LEVEL_XP_COEFFICIENT = 10


class XpSource(StrEnum):
    """XPを生んだ学習活動の種別."""

    KNOWLEDGE = "knowledge"
    TANBUN_EXPOSURE = "tanbun_exposure"
    QUIZ_CREATION = "quiz_creation"
    QUIZ_ANSWER = "quiz_answer"
    CORRECT_BONUS = "correct_bonus"


class XpBreakdown(BaseModel, frozen=True):
    """活動種別ごとの経験値."""

    knowledge: int = 0
    tanbun_exposure: int = 0
    quiz_creation: int = 0
    quiz_answer: int = 0
    correct_bonus: int = 0


class XpBreakdownItem(BaseModel, frozen=True):
    """XPの計算根拠となる活動量と単価."""

    source: XpSource
    activity_count: int
    xp_per_activity: int
    earned_xp: int


class LearningProgress(BaseModel, frozen=True):
    """学習活動にゲーム規則を適用した現在の進捗."""

    activity: LearningActivityCounts
    xp: XpBreakdown
    xp_details: list[XpBreakdownItem]
    total_xp: int
    level: int
    current_level_xp: int
    xp_for_next_level: int
    xp_to_next_level: int
    today_xp: int = 0
    level_xp_coefficient: int = DEFAULT_LEVEL_XP_COEFFICIENT


def level_threshold(level: int, coefficient: int = DEFAULT_LEVEL_XP_COEFFICIENT) -> int:
    """指定レベルに到達する累計XP."""
    return coefficient * level * (level - 1) // 2


def level_from_xp(xp: int, coefficient: int = DEFAULT_LEVEL_XP_COEFFICIENT) -> int:
    """累計XPからLvを逆算する。浮動小数点による境界誤差を避ける."""
    return (1 + isqrt(1 + 8 * (xp // coefficient))) // 2


def calculate_learning_progress(
    activity: LearningActivityCounts,
    *,
    recorded_xp: XpBreakdown | None = None,
    coefficient: int = DEFAULT_LEVEL_XP_COEFFICIENT,
) -> LearningProgress:
    """復習の事実だけからXPとLevelを計算する。知識量・作成数は加算しない."""
    xp_details = [
        _xp_detail(
            XpSource.TANBUN_EXPOSURE,
            activity.n_tanbun_exposure,
            TANBUN_EXPOSURE_XP,
        ),
        _xp_detail(
            XpSource.QUIZ_ANSWER,
            activity.n_quiz_answered,
            QUIZ_ANSWERED_XP,
        ),
        _xp_detail(
            XpSource.CORRECT_BONUS,
            activity.n_quiz_correct,
            QUIZ_CORRECT_BONUS_XP,
        ),
    ]
    if recorded_xp is not None:
        xp_details = [
            detail.model_copy(
                update={"earned_xp": getattr(recorded_xp, detail.source.value)},
            )
            for detail in xp_details
        ]
    earned_xp = {detail.source.value: detail.earned_xp for detail in xp_details}
    xp = XpBreakdown(**earned_xp)
    total_xp = sum(xp.model_dump().values())
    level = level_from_xp(total_xp, coefficient)
    current_threshold = level_threshold(level, coefficient)
    next_threshold = level_threshold(level + 1, coefficient)
    return LearningProgress(
        activity=activity,
        xp=xp,
        xp_details=xp_details,
        total_xp=total_xp,
        level=level,
        current_level_xp=total_xp - current_threshold,
        xp_for_next_level=next_threshold - current_threshold,
        xp_to_next_level=next_threshold - total_xp,
        level_xp_coefficient=coefficient,
    )


def _xp_detail(
    source: XpSource,
    activity_count: int,
    xp_per_activity: int,
) -> XpBreakdownItem:
    return XpBreakdownItem(
        source=source,
        activity_count=activity_count,
        xp_per_activity=xp_per_activity,
        earned_xp=activity_count * xp_per_activity,
    )
