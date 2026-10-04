"""Resource import同時実行制御のテスト."""

from uuid import uuid4

import pytest

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.errors import ResourceImportBusyError
from tanbun.feature.entry.resource.import_control import ResourceImportController


@mark_async_test()
async def test_rejects_overlapping_import_from_same_user() -> None:
    """同じユーザーの重複importを待機させず拒否する."""
    controller = ResourceImportController()
    user_id = uuid4()

    async with controller.slot(
        user_id,
        max_concurrent_imports=2,
        max_concurrent_imports_per_user=1,
    ):
        with pytest.raises(ResourceImportBusyError):
            async with controller.slot(
                user_id,
                max_concurrent_imports=2,
                max_concurrent_imports_per_user=1,
            ):
                pytest.fail("重複importを開始してはならない")


@mark_async_test()
async def test_rejects_import_over_global_limit() -> None:
    """別ユーザーでもサーバー全体の上限を超えて開始しない."""
    controller = ResourceImportController()

    async with controller.slot(
        uuid4(),
        max_concurrent_imports=1,
        max_concurrent_imports_per_user=1,
    ):
        with pytest.raises(ResourceImportBusyError):
            async with controller.slot(
                uuid4(),
                max_concurrent_imports=1,
                max_concurrent_imports_per_user=1,
            ):
                pytest.fail("全体上限を超えてimportを開始してはならない")
