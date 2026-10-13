"""API側の期限選択・ロールバック・504応答の検証."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j.exceptions import Neo4jError
from neomodel import adb
from starlette.responses import Response

from tanbun.api import CorsFastAPI
from tanbun.api.middleware.transaction import Neo4jTransactionMiddleware
from tanbun.config.after_commit import after_commit
from tanbun.config.database import DeadlineDriver
from tanbun.config.transactions import atomic
from tanbun.conftest import mark_async_test
from tanbun.feature.notification.usecase import dispatch_saved_notifications


def timeout_error():
    """Neo4jの実際の期限超過コードを再現する."""
    return Neo4jError._hydrate_neo4j(  # noqa: SLF001
        code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
        message="Transaction timed out",
    )


@mark_async_test()
@pytest.mark.parametrize(("method", "seconds"), [("GET", 30), ("POST", 120)])
async def test_transaction_uses_request_budget(mocker, method, seconds):
    """HTTP種別で選んだ期限がDB接続生成まで伝わる."""
    driver = DeadlineDriver(MagicMock())
    db = MagicMock(begin=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    mocker.patch("tanbun.api.middleware.transaction.adb", db)
    request = MagicMock(method=method)
    request.url.path = "/test"
    middleware = Neo4jTransactionMiddleware(None)
    observed = []

    async def begin():
        await asyncio.sleep(0)
        observed.append(driver.session().budget.timeout)

    db.begin.side_effect = begin

    async def respond(_request):
        await asyncio.sleep(0)
        observed.append(driver.session().budget.timeout)
        return Response()

    await middleware.dispatch(request, respond)
    assert observed == [seconds, seconds]
    db.commit.assert_awaited()


@mark_async_test()
async def test_commit_timeout_returns_504_and_preserves_original_error(mocker):
    """commitとrollbackの両方で失敗しても期限超過を504で返す."""
    db = MagicMock(
        begin=AsyncMock(),
        commit=AsyncMock(side_effect=timeout_error()),
        rollback=AsyncMock(side_effect=RuntimeError("closed")),
    )
    mocker.patch("tanbun.api.middleware.transaction.adb", db)
    request = MagicMock(method="POST")
    request.url.path = "/test"
    response = await Neo4jTransactionMiddleware(None).dispatch(
        request,
        AsyncMock(return_value=Response()),
    )
    assert response.status_code == 504  # noqa: PLR2004
    db.rollback.assert_awaited_once()


@mark_async_test()
async def test_cancelled_request_rolls_back(mocker):
    """CancelledErrorはExceptionでないが、ロールバックして再送出する."""
    db = MagicMock(begin=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    mocker.patch("tanbun.api.middleware.transaction.adb", db)
    request = MagicMock(method="GET")
    request.url.path = "/test"
    with pytest.raises(asyncio.CancelledError):
        await Neo4jTransactionMiddleware(None).dispatch(
            request,
            AsyncMock(side_effect=asyncio.CancelledError()),
        )
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@mark_async_test()
@pytest.mark.parametrize("response_status", [200, 400])
async def test_external_effects_run_only_after_commit(mocker, response_status):
    """Rollback never executes destructive effects, successful commit precedes them."""
    events = []
    db = MagicMock(begin=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

    async def commit():
        await asyncio.sleep(0)
        events.append("commit")

    async def effect():
        await asyncio.sleep(0)
        events.append("effect")

    db.commit.side_effect = commit
    mocker.patch("tanbun.api.middleware.transaction.adb", db)
    request = MagicMock(method="PATCH")
    request.url.path = "/test"

    async def respond(_request):
        after_commit(effect)
        await asyncio.sleep(0)
        return Response(status_code=response_status)

    await Neo4jTransactionMiddleware(None).dispatch(request, respond)
    assert events == (["commit", "effect"] if response_status == 200 else [])  # noqa: PLR2004


@mark_async_test()
async def test_parallel_http_transactions_include_real_orm_queries():
    """実際のHTTP子タスクからのORM書き込みも、別リクエストとは分離される."""
    app = CorsFastAPI()
    app.add_middleware(Neo4jTransactionMiddleware)
    barrier = asyncio.Barrier(2)

    @app.post("/{value}")
    async def write(value: str):
        async with atomic():
            await adb.cypher_query(
                "CREATE (:HttpIsolationProbe {value:$value})",
                {"value": value},
            )
        await barrier.wait()
        return Response(status_code=200 if value == "kept" else 409)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        responses = await asyncio.wait_for(
            asyncio.gather(client.post("/kept"), client.post("/removed")),
            timeout=10,
        )
    assert [response.status_code for response in responses] == [200, 409]
    rows, _ = await adb.cypher_query("MATCH (p:HttpIsolationProbe) RETURN p.value")
    assert rows == [["kept"]]


@mark_async_test()
@pytest.mark.parametrize("response_status", [200, 409])
async def test_push_waits_for_real_http_transaction_commit(mocker, response_status):
    """通知を送ってからDBロールバックする、という順序の逆転を防ぐ."""
    app = CorsFastAPI()
    app.add_middleware(Neo4jTransactionMiddleware)
    observed = []

    async def send(*_args):  # noqa: RUF029
        observed.append(adb._active_transaction is None)  # noqa: SLF001

    push = mocker.patch(
        "tanbun.feature.notification.usecase._dispatch_web_push",
        side_effect=send,
    )

    @app.post("/test")
    async def write():
        async with atomic():
            await dispatch_saved_notifications("user", [MagicMock()])
        push.assert_not_awaited()
        return Response(status_code=response_status)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.post("/test")
    assert observed == ([True] if response_status == 200 else [])  # noqa: PLR2004
