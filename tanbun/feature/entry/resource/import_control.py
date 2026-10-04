"""Resource importの同時実行を制限する."""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.entry.errors import ResourceImportBusyError


class ResourceImportController:
    """待機させずに同期importの実行枠を貸し出す."""

    def __init__(self) -> None:
        """空の実行枠を作る."""
        self._lock = asyncio.Lock()
        self._active_imports = 0
        self._imports_by_user: defaultdict[str, int] = defaultdict(int)

    @asynccontextmanager
    async def slot(
        self,
        user_id: UUIDy,
        *,
        max_concurrent_imports: int,
        max_concurrent_imports_per_user: int,
    ) -> AsyncIterator[None]:
        """空きがあれば枠を確保し、なければ即座に拒否する."""
        key = to_uuid(user_id).hex
        async with self._lock:
            if self._imports_by_user[key] >= max_concurrent_imports_per_user:
                msg = "別の読書メモをimport中です。完了後に再送してください"
                raise ResourceImportBusyError(msg=msg, headers={"Retry-After": "5"})
            if self._active_imports >= max_concurrent_imports:
                msg = "現在importが混み合っています。少し待ってから再送してください"
                raise ResourceImportBusyError(msg=msg, headers={"Retry-After": "5"})
            self._active_imports += 1
            self._imports_by_user[key] += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active_imports -= 1
                self._imports_by_user[key] -= 1
                if self._imports_by_user[key] == 0:
                    del self._imports_by_user[key]


resource_import_controller = ResourceImportController()
