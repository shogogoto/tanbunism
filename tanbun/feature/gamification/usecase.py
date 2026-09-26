"""学習進捗のusecase."""

from tanbun.feature.domain.types import UUIDy
from tanbun.feature.learning_activity.repo import fetch_learning_activity_counts

from .domain import LearningProgress, calculate_learning_progress


async def fetch_learning_progress(user_id: UUIDy) -> LearningProgress:
    """ユーザーの現在の学習進捗を返す."""
    activity = await fetch_learning_activity_counts(user_id)
    return calculate_learning_progress(activity)
