"""Only complete frontiers backed by usable quiz populations."""
# ruff: noqa: SLF001, PLR2004

from asyncio import run
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from . import preparation


@pytest.mark.parametrize("available", [5, 8])
def test_incomplete_population_stays_pending(monkeypatch, available):
    """Zero and partial generation never complete a frontier."""
    complete, generate, user_id, resource_id = setup_preparation(monkeypatch)
    pool = {"ready": False, "required_quizzes": 10, "available_quizzes": available}
    monkeypatch.setattr(
        preparation,
        "get_or_prepare_region_pool",
        AsyncMock(return_value=pool),
    )
    run(preparation._prepare_resource_to_frontier(user_id, resource_id))
    assert generate.await_args.args[2] == 10 - available
    complete.assert_not_awaited()


@pytest.mark.parametrize("already_ready", [False, True])
def test_complete_only_when_population_is_ready(monkeypatch, already_ready):
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
    run(preparation._prepare_resource_to_frontier(user_id, resource_id))
    complete.assert_awaited_once()
    assert pool.await_args.args[2] == 2
    if already_ready:
        generate.assert_not_awaited()
    else:
        assert generate.await_args.args[2] == 2


def setup_preparation(monkeypatch):
    """No live DB or external generation in these orchestration tests."""
    user_id, resource_id, plan_id = uuid4(), str(uuid4()), uuid4()
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


def test_legacy_completion_is_limited_by_usable_quizzes(monkeypatch):
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
    assert run(preparation.dungeon_quiz_progress(user_id, resource_id)) == (plan_id, 0)


def test_repairs_legacy_completion_without_incrementing_it(monkeypatch):
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
    run(preparation._prepare_resource_to_frontier(user_id, resource_id))
    assert generate.await_args.args[2] == 5
    complete.assert_not_awaited()


def test_schedules_repair_for_legacy_counter_ahead_of_quiz_count(monkeypatch):
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
    run(preparation.schedule_dungeon_quizzes(user_id, object(), tasks, [resource_id]))
    reserve.assert_awaited_once()
    assert len(tasks.tasks) == 1
