"""ダンジョンの開拓に合わせて既定StudyPlanのクイズを補充する."""

import logging
from uuid import UUID

from fastapi import BackgroundTasks
from neomodel import adb

from tanbun.feature.entry.resource.repo.owner import check_entry_owner
from tanbun.feature.quiz.learning.study_plan.preparation_control import (
    UserPreparationLimitError,
    quiz_preparation_controller,
)
from tanbun.feature.quiz.learning.study_plan.preparation_settings import (
    get_quiz_preparation_settings,
)
from tanbun.feature.quiz.learning.study_plan.repo import (
    adventure_quiz_progress,
    complete_adventure_quiz_region,
    count_prepared_quizzes,
    ensure_default_resource_study_plan,
)
from tanbun.feature.quiz.learning.study_plan.usecase import (
    prepare_additional_study_plan_quizzes,
)

from .population import REGION_QUIZ_COUNT, get_or_prepare_region_pool
from .state import GameState, read_state

logger = logging.getLogger(__name__)
PLACES_PER_REGION = 5
MAX_ADVENTURE_REGIONS = 19
_scheduled_resources: set[tuple[str, str]] = set()


async def dungeon_quiz_progress(
    user_id: UUID,
    resource_id: str,
) -> tuple[UUID | None, int]:
    """Legacy completion counters never advertise more than usable quizzes allow."""
    plan_id, recorded = await adventure_quiz_progress(user_id, resource_id)
    if not plan_id or not recorded:
        return plan_id, 0
    count = await count_prepared_quizzes(plan_id, user_id)
    return plan_id, min(recorded, max(0, count // REGION_QUIZ_COUNT - 1))


def _frontier(state: GameState, resource_id: str) -> int:
    """開拓済み地点5件ごとの追加準備回数."""
    key = resource_id.replace("-", "").lower()
    map_state = next(
        (
            value
            for stored_id, value in state.save.maps.items()
            if stored_id.replace("-", "").lower() == key
        ),
        None,
    )
    return (
        min(len(map_state.places) // PLACES_PER_REGION, MAX_ADVENTURE_REGIONS)
        if map_state
        else 0
    )


async def _prepare_resource_to_frontier(user_id: UUID, resource_id: str) -> None:
    """開拓済みの未準備領域を順に生成。次の保存・状態確認でも再試行可能."""
    if not await check_entry_owner(user_id, resource_id):
        return
    rows, _ = await adb.cypher_query(
        "MATCH (r:Resource {uid: $uid}) RETURN r.title",
        {"uid": resource_id.replace("-", "").lower()},
    )
    resource_name = rows[0][0] if rows else "ダンジョン"
    await ensure_default_resource_study_plan(
        user_id,
        resource_id,
        resource_name or "ダンジョン",
    )
    while True:
        state = await read_state(user_id)
        target = _frontier(state, resource_id)
        plan_id, prepared_regions = await adventure_quiz_progress(
            user_id,
            resource_id,
        )
        if not plan_id or not target:
            return
        # Region zero needs the initial five quizzes; each frontier adds five.
        # Use the actual usable population, not generation attempts, as completion.
        level = min(prepared_regions + 2, target + 1)
        pool = await get_or_prepare_region_pool(user_id, resource_id, level)
        if not pool["ready"]:
            await prepare_additional_study_plan_quizzes(
                plan_id,
                user_id,
                int(pool["required_quizzes"]) - int(pool["available_quizzes"]),
                send_notification=False,
            )
            pool = await get_or_prepare_region_pool(user_id, resource_id, level)
        if not pool["ready"]:
            logger.info(
                "Dungeon resource %s still lacks usable quizzes at region %d",
                resource_id,
                level,
            )
            # Partial preparation is retained; a later queued attempt fills only
            # the remaining deficit, without marking this frontier complete.
            return
        if target <= prepared_regions:
            # Repair legacy over-recorded completion without double-counting it.
            return
        if not await complete_adventure_quiz_region(
            user_id,
            plan_id,
            prepared_regions,
        ):
            # Another request for the same account finished this frontier.
            continue
        if target == prepared_regions + 1:
            return


async def prepare_dungeon_quizzes(user_id: UUID, resource_ids: list[str]) -> None:
    """Queued preparation operation shared by game state writes and status polls."""
    try:
        for resource_id in resource_ids:
            try:
                await _prepare_resource_to_frontier(user_id, resource_id)
            except Exception:
                logger.exception("Dungeon quiz preparation failed for %s", resource_id)
    finally:
        _scheduled_resources.difference_update(
            (user_id.hex, resource_id.replace("-", "").lower())
            for resource_id in resource_ids
        )


async def schedule_dungeon_quizzes(
    user_id: UUID,
    state: GameState,
    background_tasks: BackgroundTasks,
    resource_ids: list[str] | None = None,
) -> None:
    """準備待ちのResourceを既存クイズ準備キューへ予約する."""
    pending: list[str] = []
    user_key = user_id.hex
    candidates = resource_ids or ([state.save.run.resourceId] if state.save.run else [])
    for resource_id in candidates:
        key = (user_key, resource_id.replace("-", "").lower())
        if key in _scheduled_resources:
            continue
        target = _frontier(state, resource_id)
        plan_id, prepared_regions = await dungeon_quiz_progress(
            user_id,
            resource_id,
        )
        owned = plan_id or await check_entry_owner(user_id, resource_id)
        if target > prepared_regions and owned:
            _scheduled_resources.add(key)
            pending.append(resource_id)
    if not pending:
        return
    settings = await get_quiz_preparation_settings()
    try:
        await quiz_preparation_controller.reserve(
            user_id,
            max_jobs_per_user=settings.max_concurrent_jobs_per_user,
        )
    except UserPreparationLimitError:
        _scheduled_resources.difference_update(
            (user_key, resource_id.replace("-", "").lower()) for resource_id in pending
        )
        return
    background_tasks.add_task(
        quiz_preparation_controller.execute,
        user_id,
        max_concurrent_jobs=settings.max_concurrent_jobs,
        operation=prepare_dungeon_quizzes,
        args=(user_id, pending),
    )


def preparation_status(
    state: GameState,
    prepared_regions: int,
    resource_id: str,
) -> dict:
    """UI向けの到達領域・準備済み領域."""
    return {
        "prepared_regions": prepared_regions,
        "target_regions": _frontier(state, resource_id),
    }
