"""経験値とレベルのモデル・規則."""

from enum import StrEnum
from math import isqrt

from pydantic import BaseModel

from tanbun.feature.learning_activity.domain import LearningActivityCounts

SENTENCE_XP = 1
QUIZ_CREATED_XP = 1
QUIZ_ANSWERED_XP = 5
QUIZ_CORRECT_BONUS_XP = 2
LEVEL_CURVE = 50


class XpSource(StrEnum):
    """XPを生んだ学習活動の種別."""

    KNOWLEDGE = "knowledge"
    QUIZ_CREATION = "quiz_creation"
    QUIZ_ANSWER = "quiz_answer"
    CORRECT_BONUS = "correct_bonus"


class XpBreakdown(BaseModel, frozen=True):
    """活動種別ごとの経験値."""

    knowledge: int
    quiz_creation: int
    quiz_answer: int
    correct_bonus: int


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


def level_threshold(level: int) -> int:
    """指定レベルに到達する累計XP."""
    return LEVEL_CURVE * (level - 1) ** 2


def calculate_learning_progress(
    activity: LearningActivityCounts,
) -> LearningProgress:
    """活動の事実から、永続化せずXPとLevelを計算する."""
    xp_details = [
        _xp_detail(XpSource.KNOWLEDGE, activity.n_sentence, SENTENCE_XP),
        _xp_detail(
            XpSource.QUIZ_CREATION,
            activity.n_quiz_created,
            QUIZ_CREATED_XP,
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
    earned_xp = {detail.source.value: detail.earned_xp for detail in xp_details}
    xp = XpBreakdown(**earned_xp)
    total_xp = sum(xp.model_dump().values())
    level = isqrt(total_xp // LEVEL_CURVE) + 1
    current_threshold = level_threshold(level)
    next_threshold = level_threshold(level + 1)
    return LearningProgress(
        activity=activity,
        xp=xp,
        xp_details=xp_details,
        total_xp=total_xp,
        level=level,
        current_level_xp=total_xp - current_threshold,
        xp_for_next_level=next_threshold - current_threshold,
        xp_to_next_level=next_threshold - total_xp,
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
