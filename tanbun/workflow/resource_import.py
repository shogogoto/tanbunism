"""Resource import後に実行するfeature間ワークフロー."""

from tanbun.feature.domain.types import UUIDy
from tanbun.feature.entry.mapper import MResource
from tanbun.feature.quiz.learning.study_plan.repo import (
    ensure_default_resource_study_plan,
)


async def prepare_imported_resource_learning(
    user_id: UUIDy,
    resource: MResource,
) -> None:
    """取り込み済みResourceをすぐ学習計画で使えるようにする."""
    await ensure_default_resource_study_plan(
        user_id,
        resource.uid,
        resource.name,
    )
