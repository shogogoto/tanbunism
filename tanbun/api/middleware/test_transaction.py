"""API側の期限選択・ロールバック・504応答の検証."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j.exceptions import Neo4jError
from starlette.responses import Response

from tanbun.api.middleware.transaction import Neo4jTransactionMiddleware
from tanbun.config.database import DeadlineDriver
from tanbun.conftest import mark_async_test


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
    mocker.patch("tanbun.api.middleware.transaction.AsyncDatabase", return_value=db)
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
    mocker.patch("tanbun.api.middleware.transaction.AsyncDatabase", return_value=db)
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
    mocker.patch("tanbun.api.middleware.transaction.AsyncDatabase", return_value=db)
    request = MagicMock(method="GET")
    request.url.path = "/test"
    with pytest.raises(asyncio.CancelledError):
        await Neo4jTransactionMiddleware(None).dispatch(
            request,
            AsyncMock(side_effect=asyncio.CancelledError()),
        )
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
