"""並行HTTP、ORM参加、閉じたtransactionの参照残留を検証."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from neomodel import adb
from neomodel.async_.core import AsyncDatabase

from tanbun.config.transactions import atomic, database_scope
from tanbun.conftest import mark_async_test
from tanbun.feature.user.label import LUser


@mark_async_test()
async def test_parallel_requests_commit_and_rollback_independently():
    """別のHTTPが同時に書いても、確定・破棄の対象は混ざらない."""
    barrier = asyncio.Barrier(2)

    async def write(value, commit):
        with database_scope():
            await adb.begin()
            transaction = adb._active_transaction  # noqa: SLF001
            await adb.cypher_query(
                "CREATE (:TransactionIsolationProbe {value:$value})",
                {"value": value},
            )
            await barrier.wait()
            assert adb._active_transaction is transaction  # noqa: SLF001
            if commit:
                await adb.commit()
            else:
                await adb.rollback()
            return transaction

    first, second = await asyncio.gather(
        write("kept", commit=True),
        write("removed", commit=False),
    )
    assert first is not second
    rows, _ = await adb.cypher_query(
        "MATCH (p:TransactionIsolationProbe) RETURN p.value",
    )
    assert rows == [["kept"]]


@mark_async_test()
async def test_http_child_orm_and_nested_atomic_use_outer_transaction():
    """BaseHTTPの子タスクとORMが、親の未確定データを読める."""
    with database_scope():
        await adb.begin()
        transaction = adb._active_transaction  # noqa: SLF001

        async def child():
            async with atomic():
                assert adb._active_transaction is transaction  # noqa: SLF001
                await adb.cypher_query("CREATE (:User {uid:'isolation-probe'})")
                assert await LUser.nodes.get_or_none(uid="isolation-probe") is not None

        await asyncio.create_task(child())
        assert adb._active_transaction is transaction  # noqa: SLF001
        await adb.rollback()
    assert await LUser.nodes.get_or_none(uid="isolation-probe") is None


@mark_async_test()
async def test_separate_task_does_not_use_closed_transaction_during_commit(mocker):
    """close途中の別タスクのユーザー取得でもTransaction closedにしない."""
    entered, finish = asyncio.Event(), asyncio.Event()

    async def writer():
        async with atomic():
            session = adb._session  # noqa: SLF001
            close = session.close

            async def delayed_close():
                entered.set()
                await finish.wait()
                await close()

            mocker.patch.object(session, "close", side_effect=delayed_close)

    task = asyncio.create_task(writer())
    await entered.wait()
    try:
        assert await LUser.nodes.get_or_none(uid="missing-user") is None
    finally:
        finish.set()
        await task


@mark_async_test()
async def test_close_failure_clears_transaction_and_session():
    """後片付けの例外が出ても、次の問い合わせへ閉じた参照を渡さない."""
    db = AsyncDatabase()
    db.driver = MagicMock()
    db._active_transaction = MagicMock(commit=AsyncMock(), close=AsyncMock())  # noqa: SLF001
    db._session = MagicMock(  # noqa: SLF001
        last_bookmarks=AsyncMock(),
        close=AsyncMock(side_effect=RuntimeError("close failed")),
    )
    with pytest.raises(RuntimeError, match="close failed"):
        await db.commit()
    assert db._active_transaction is None  # noqa: SLF001
    assert db._session is None  # noqa: SLF001


@mark_async_test()
async def test_finished_request_does_not_leak_transaction_into_background_task():
    """終了したHTTPから継承したcontextは、後から使っても新規扱いにする."""
    finish = asyncio.Event()

    async def delayed_child():
        await finish.wait()
        assert adb._active_transaction is None  # noqa: SLF001
        async with atomic():
            await adb.cypher_query("CREATE (:TransactionIsolationProbe)")

    with database_scope():
        await adb.begin()
        task = asyncio.create_task(delayed_child())
        await adb.rollback()
    finish.set()
    await task
    rows, _ = await adb.cypher_query(
        "MATCH (p:TransactionIsolationProbe) RETURN count(p)",
    )
    assert rows == [[1]]


@mark_async_test()
async def test_caught_nested_failure_still_rolls_back_outer_transaction():
    """内側の例外を呼び出し元が捕捉しても途中の書き込みを確定しない."""

    async def operation():
        async with atomic():
            try:
                async with atomic():
                    await adb.cypher_query("CREATE (:TransactionIsolationProbe)")
                    msg = "nested failure"
                    raise ValueError(msg)  # noqa: TRY301
            except ValueError:
                pass

    with pytest.raises(RuntimeError, match="marked for rollback"):
        await operation()
    rows, _ = await adb.cypher_query(
        "MATCH (p:TransactionIsolationProbe) RETURN count(p)",
    )
    assert rows == [[0]]
