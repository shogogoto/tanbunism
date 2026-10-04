"""一括クイズ準備をWebプロセス内で直列化する."""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from tanbun.feature.domain.types import UUIDy, to_uuid

T = TypeVar("T")


class UserPreparationLimitError(Exception):
    """同一ユーザーの待機・実行ジョブが上限へ達した."""


class QuizPreparationController:
    """予約済みジョブを設定された同時実行数で処理する."""

    def __init__(self) -> None:
        """空の実行枠を作る."""
        self._condition = asyncio.Condition()
        self._active_jobs = 0
        self._jobs_by_user: defaultdict[str, int] = defaultdict(int)

    async def reserve(self, user_id: UUIDy, *, max_jobs_per_user: int) -> None:
        """投入前にユーザー枠を確保する."""
        key = to_uuid(user_id).hex
        async with self._condition:
            if self._jobs_by_user[key] >= max_jobs_per_user:
                msg = "クイズを準備中です。完了通知を待ってから再実行してください"
                raise UserPreparationLimitError(msg)
            self._jobs_by_user[key] += 1

    async def execute(
        self,
        user_id: UUIDy,
        *,
        max_concurrent_jobs: int,
        operation: Callable[..., Awaitable[T]],
        args: tuple = (),
    ) -> T:
        """全体枠が空くまで待機し、予約した処理を実行する."""
        key = to_uuid(user_id).hex
        acquired = False
        try:
            async with self._condition:
                await self._condition.wait_for(
                    lambda: self._active_jobs < max_concurrent_jobs,
                )
                self._active_jobs += 1
                acquired = True
            return await operation(*args)
        finally:
            async with self._condition:
                if acquired:
                    self._active_jobs -= 1
                self._jobs_by_user[key] = max(0, self._jobs_by_user[key] - 1)
                if self._jobs_by_user[key] == 0:
                    del self._jobs_by_user[key]
                self._condition.notify_all()


quiz_preparation_controller = QuizPreparationController()
