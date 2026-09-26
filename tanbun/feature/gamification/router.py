"""公開学習進捗API."""

from uuid import UUID

from fastapi import APIRouter

from .domain import LearningProgress
from .usecase import fetch_learning_progress

router = APIRouter(prefix="/user", tags=["gamification"])


@router.get("/{user_id}/learning-progress")
async def get_learning_progress(user_id: UUID) -> LearningProgress:
    """公開プロフィール用のLevelとXPを取得する."""
    return await fetch_learning_progress(user_id)


def gamification_router() -> APIRouter:
    """ゲーム要素のrouterを返す."""
    return router
