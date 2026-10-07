"""公開学習進捗API."""

from uuid import UUID

from fastapi import APIRouter

from tanbun.feature.user.router_util import ActiveUser

from .domain import LearningProgress
from .resource import ResourceGrowthResult, ResourceGrowthRules, fetch_resource_growth
from .settings import get_gamification_settings
from .usecase import fetch_learning_progress

router = APIRouter(prefix="/user", tags=["gamification"])


@router.get("/me/resource-growth")
async def get_resource_growth(user: ActiveUser) -> ResourceGrowthResult:
    """本人のリソース別復習実績とPowerの内訳."""
    settings = await get_gamification_settings()
    return ResourceGrowthResult(
        resources=await fetch_resource_growth(
            user.uid,
            coefficient=settings.level_xp_coefficient,
        ),
        rules=ResourceGrowthRules(**settings.model_dump()),
    )


@router.get("/{user_id}/learning-progress")
async def get_learning_progress(user_id: UUID) -> LearningProgress:
    """公開プロフィール用のLevelとXPを取得する."""
    return await fetch_learning_progress(user_id)


@router.get("/{user_id}/resource-growth")
async def get_public_resource_growth(user_id: UUID) -> ResourceGrowthResult:
    """公開本棚には所有リソースの成長集計だけを返す."""
    settings = await get_gamification_settings()
    resources = await fetch_resource_growth(
        user_id,
        owned_only=True,
        coefficient=settings.level_xp_coefficient,
    )
    return ResourceGrowthResult(
        resources=[
            resource.model_copy(update={"recent_xp": []}) for resource in resources
        ],
        rules=ResourceGrowthRules(**settings.model_dump()),
    )


def gamification_router() -> APIRouter:
    """ゲーム要素のrouterを返す."""
    return router
