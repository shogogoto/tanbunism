"""一括クイズ準備の実行制御テスト."""

import asyncio
from uuid import uuid4

import pytest

from tanbun.conftest import mark_async_test
from tanbun.feature.quiz.learning.study_plan.preparation_control import (
    QuizPreparationController,
    UserPreparationLimitError,
)


@mark_async_test()
async def test_rejects_another_job_from_same_user() -> None:
    """同じユーザーが上限を超えてジョブを予約できない."""
    controller = QuizPreparationController()
    user_id = uuid4()
    await controller.reserve(user_id, max_jobs_per_user=1)

    with pytest.raises(UserPreparationLimitError):
        await controller.reserve(user_id, max_jobs_per_user=1)

    await controller.execute(
        user_id,
        max_concurrent_jobs=1,
        operation=_nothing,
    )


@mark_async_test()
async def test_runs_jobs_within_global_concurrency() -> None:
    """異なるユーザーのジョブも全体上限1なら直列に実行する."""
    controller = QuizPreparationController()
    first_user = uuid4()
    second_user = uuid4()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def first() -> None:
        first_started.set()
        await release_first.wait()

    async def second() -> None:
        await asyncio.sleep(0)
        second_started.set()

    await controller.reserve(first_user, max_jobs_per_user=1)
    await controller.reserve(second_user, max_jobs_per_user=1)
    first_task = asyncio.create_task(
        controller.execute(
            first_user,
            max_concurrent_jobs=1,
            operation=first,
        ),
    )
    await first_started.wait()
    second_task = asyncio.create_task(
        controller.execute(
            second_user,
            max_concurrent_jobs=1,
            operation=second,
        ),
    )
    await asyncio.sleep(0)

    assert not second_started.is_set()
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_started.is_set()


async def _nothing() -> None:
    """完了するだけのジョブ."""


@mark_async_test()
async def test_timeout_releases_reservation_for_retry(monkeypatch) -> None:
    """停止した処理がユーザー枠と全体枠を占有し続けない."""
    monkeypatch.setattr(
        "tanbun.feature.quiz.learning.study_plan.preparation_control.PREPARATION_TIMEOUT_SECONDS",
        0.01,
    )
    controller = QuizPreparationController()
    user_id = uuid4()
    await controller.reserve(user_id, max_jobs_per_user=1)
    blocked = asyncio.Event()
    with pytest.raises(TimeoutError):
        await controller.execute(user_id, max_concurrent_jobs=1, operation=blocked.wait)
    await controller.reserve(user_id, max_jobs_per_user=1)
    await controller.execute(user_id, max_concurrent_jobs=1, operation=_nothing)
