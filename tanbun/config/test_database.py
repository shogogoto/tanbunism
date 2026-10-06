"""共通期限のdriver互換境界と、Neo4j側での実際の中断を検証."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j import Query
from neo4j.exceptions import Neo4jError
from neomodel import adb
from neomodel.async_.core import AsyncDatabase
from pydantic import ValidationError

from tanbun.config.database import (
    DatabaseBudget,
    DeadlineDriver,
    DeadlineSession,
    configure_database_deadlines,
    database_budget,
    is_database_timeout,
)
from tanbun.config.env import Settings
from tanbun.conftest import mark_async_test


def test_configured_driver_wraps_reconnections_once(mocker):
    """再設定・再接続でも期限アダプターを失わず、多重に包まない."""
    raw = mocker.patch("neomodel.async_.core.AsyncGraphDatabase.driver").return_value
    connection = SimpleNamespace(_database_name=None)
    configure_database_deadlines(30)
    configure_database_deadlines(30)
    for _ in range(2):
        AsyncDatabase._parse_driver_from_url(  # noqa: SLF001
            connection,
            "bolt://user:password@localhost:7687",
        )
        assert isinstance(connection.driver, DeadlineDriver)
        assert connection.driver.driver is raw


@mark_async_test()
async def test_implicit_query_has_server_deadline_and_metadata():
    """ORMから渡される文字列もサーバー期限付きQueryになる."""
    raw = MagicMock(run=AsyncMock())
    session = DeadlineSession(raw, DatabaseBudget(30, "GET /tanbun"))
    await session.run("RETURN $value", parameters={"value": 1})
    query = raw.run.call_args.args[0]
    assert isinstance(query, Query)
    assert query.timeout == 30  # noqa: PLR2004
    assert query.metadata == {"app": "tanbunism", "operation": "GET /tanbun"}
    assert raw.run.call_args.kwargs["parameters"] == {"value": 1}


@mark_async_test()
@pytest.mark.parametrize(
    ("specified", "expected"),
    [(None, 30), (0, 30), (300, 30), (5, 5)],
)
async def test_explicit_transaction_cannot_remove_deadline(specified, expected):
    """明示トランザクションでも共通上限を超えない."""
    raw = MagicMock(begin_transaction=AsyncMock())
    session = DeadlineSession(raw, DatabaseBudget(30))
    await session.begin_transaction(timeout=specified)
    assert raw.begin_transaction.call_args.kwargs["timeout"] == expected


@mark_async_test()
async def test_existing_query_keeps_metadata_and_shorter_timeout():
    """呼び出し側の短い期限とメタ情報は失わない."""
    raw = MagicMock(run=AsyncMock())
    session = DeadlineSession(raw, DatabaseBudget(30))
    await session.run(Query("RETURN 1", timeout=2, metadata={"job": "test"}))
    query = raw.run.call_args.args[0]
    assert query.timeout == 2  # noqa: PLR2004
    assert query.metadata["job"] == "test"


@mark_async_test()
async def test_budgets_are_isolated_between_tasks():
    """並行する取得・更新で期限を共有しない."""
    driver = DeadlineDriver(MagicMock())

    async def capture(seconds):
        with database_budget(seconds, str(seconds)):
            await asyncio.sleep(0)
            return driver.session().budget.timeout

    assert await asyncio.gather(capture(30), capture(120)) == [30, 120]
    assert driver.session().budget.timeout == 30  # noqa: PLR2004


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
@pytest.mark.parametrize(
    "field",
    [
        "NEO4J_READ_TIMEOUT_SECONDS",
        "NEO4J_WRITE_TIMEOUT_SECONDS",
        "NEO4J_SCHEMA_TIMEOUT_SECONDS",
    ],
)
def test_settings_reject_unbounded_deadlines(timeout, field):
    """環境変数でも期限なしにはできない."""
    with pytest.raises(ValidationError):
        Settings(**{field: timeout})


@mark_async_test()
@pytest.mark.parametrize("explicit", [False, True])
async def test_server_terminates_timed_out_queries(explicit):
    """クライアント待機だけでなく、サーバー側で処理が終了する."""
    with database_budget(0.1, "deadline regression"):
        if explicit:
            await adb.begin()
            await adb.cypher_query("CREATE (:DeadlineTestMarker)")
        try:
            with pytest.raises(Neo4jError) as captured:
                await asyncio.wait_for(
                    adb.cypher_query(
                        "UNWIND range(1, 100000) AS a "
                        "UNWIND range(1, 100000) AS b RETURN sum(a + b)",
                    ),
                    timeout=10,
                )
            assert is_database_timeout(captured.value)
        finally:
            if explicit:
                await adb.rollback()
    rows, _ = await adb.cypher_query("MATCH (n:DeadlineTestMarker) RETURN count(n)")
    assert rows == [[0]]
    rows, _ = await adb.cypher_query(
        "SHOW TRANSACTIONS YIELD metaData "
        "WHERE metaData.operation = 'deadline regression' RETURN count(*)",
    )
    assert rows == [[0]]
