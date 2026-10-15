"""Only complete frontiers backed by usable quiz populations."""
# ruff: noqa: SLF001, PLR2004

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from tanbun.conftest import mark_async_test

from . import preparation


@pytest.mark.parametrize("available", [5, 8])
@mark_async_test()
async def test_incomplete_population_stays_pending(monkeypatch, available):
    """Zero and partial generation never complete a frontier."""
    complete, generate, user_id, resource_id = setup_preparation(monkeypatch)
    pool = {"ready": False, "required_quizzes": 10, "available_quizzes": available}
    monkeypatch.setattr(
        preparation,
        "get_or_prepare_region_pool",
        AsyncMock(return_value=pool),
    )
    await preparation._prepare_resource_to_frontier(user_id, resource_id)
    assert generate.await_args.args[2] == 10 - available
    complete.assert_not_awaited()


@pytest.mark.parametrize("already_ready", [False, True])
@mark_async_test()
async def test_complete_only_when_population_is_ready(monkeypatch, already_ready):
    """Retries fill the deficit; already prepared quizzes need no generation."""
    complete, generate, user_id, resource_id = setup_preparation(monkeypatch)
    ready = {"ready": True, "required_quizzes": 10, "available_quizzes": 10}
    responses = (
        [ready]
        if already_ready
        else [
            {"ready": False, "required_quizzes": 10, "available_quizzes": 8},
            ready,
        ]
    )
    pool = AsyncMock(side_effect=responses)
    monkeypatch.setattr(preparation, "get_or_prepare_region_pool", pool)
    await preparation._prepare_resource_to_frontier(user_id, resource_id)
    complete.assert_awaited_once()
    assert pool.await_args.args[2] == 2
    if already_ready:
        generate.assert_not_awaited()
    else:
        assert generate.await_args.args[2] == 2


def setup_preparation(monkeypatch):
    """No live DB or external generation in these orchestration tests."""
    user_id, resource_id, plan_id = uuid4(), str(uuid4()), uuid4()
    monkeypatch.setattr(preparation, "preparation_states", AsyncMock(return_value={}))
    monkeypatch.setattr(preparation, "set_preparation_state", AsyncMock())
    monkeypatch.setattr(preparation, "check_entry_owner", AsyncMock(return_value=True))
    monkeypatch.setattr(
        preparation.adb,
        "cypher_query",
        AsyncMock(return_value=([["Book"]], None)),
    )
    monkeypatch.setattr(preparation, "ensure_default_resource_study_plan", AsyncMock())
    monkeypatch.setattr(preparation, "read_state", AsyncMock())
    monkeypatch.setattr(preparation, "_frontier", lambda *_: 1)
    monkeypatch.setattr(
        preparation,
        "adventure_quiz_progress",
        AsyncMock(side_effect=[(plan_id, 0), (plan_id, 1)]),
    )
    complete, generate = AsyncMock(return_value=True), AsyncMock()
    monkeypatch.setattr(preparation, "complete_adventure_quiz_region", complete)
    monkeypatch.setattr(preparation, "prepare_additional_study_plan_quizzes", generate)
    return complete, generate, user_id, resource_id


@mark_async_test()
async def test_legacy_completion_is_limited_by_usable_quizzes(monkeypatch):
    """A previously over-recorded frontier must become pending again."""
    plan_id, user_id, resource_id = uuid4(), uuid4(), str(uuid4())
    monkeypatch.setattr(
        preparation,
        "adventure_quiz_progress",
        AsyncMock(return_value=(plan_id, 3)),
    )
    monkeypatch.setattr(
        preparation,
        "count_prepared_quizzes",
        AsyncMock(return_value=8),
    )
    assert await preparation.dungeon_quiz_progress(user_id, resource_id) == (plan_id, 0)


@mark_async_test()
async def test_newly_opened_dungeon_prepares_initial_quizzes_before_any_exploration(
    monkeypatch,
):
    """開拓0地点でも最初の領域の5問を準備する."""
    complete, generate, user, resource = setup_preparation(monkeypatch)
    monkeypatch.setattr(preparation, "_frontier", lambda *_: 0)
    monkeypatch.setattr(
        preparation,
        "get_or_prepare_region_pool",
        AsyncMock(
            side_effect=[
                {"ready": False, "required_quizzes": 5, "available_quizzes": 0},
                {"ready": True, "required_quizzes": 5, "available_quizzes": 5},
            ],
        ),
    )
    await preparation._prepare_resource_to_frontier(user, resource)
    assert generate.await_args.args[2] == 5
    complete.assert_not_awaited()


@mark_async_test()
async def test_repairs_legacy_completion_without_incrementing_it(monkeypatch):
    """Repair missing quizzes even if the stored completion counter is ahead."""
    complete, generate, user_id, resource_id = setup_preparation(monkeypatch)
    monkeypatch.setattr(
        preparation,
        "adventure_quiz_progress",
        AsyncMock(return_value=(uuid4(), 3)),
    )
    monkeypatch.setattr(
        preparation,
        "get_or_prepare_region_pool",
        AsyncMock(
            side_effect=[
                {"ready": False, "required_quizzes": 10, "available_quizzes": 5},
                {"ready": True, "required_quizzes": 10, "available_quizzes": 10},
            ],
        ),
    )
    await preparation._prepare_resource_to_frontier(user_id, resource_id)
    assert generate.await_args.args[2] == 5
    complete.assert_not_awaited()


@mark_async_test()
async def test_schedules_repair_for_legacy_counter_ahead_of_quiz_count(monkeypatch):
    """Status polling queues repairs rather than trusting a stale completion flag."""
    _, _, user_id, resource_id = setup_preparation(monkeypatch)
    monkeypatch.setattr(preparation, "_scheduled_resources", set())
    monkeypatch.setattr(
        preparation,
        "adventure_quiz_progress",
        AsyncMock(return_value=(uuid4(), 3)),
    )
    monkeypatch.setattr(
        preparation,
        "count_prepared_quizzes",
        AsyncMock(return_value=8),
    )
    monkeypatch.setattr(
        preparation,
        "get_quiz_preparation_settings",
        AsyncMock(
            return_value=SimpleNamespace(
                max_concurrent_jobs_per_user=1,
                max_concurrent_jobs=1,
            ),
        ),
    )
    reserve = AsyncMock()
    monkeypatch.setattr(preparation.quiz_preparation_controller, "reserve", reserve)
    tasks = BackgroundTasks()
    await preparation.schedule_dungeon_quizzes(user_id, object(), tasks, [resource_id])
    reserve.assert_awaited_once()
    assert len(tasks.tasks) == 1
