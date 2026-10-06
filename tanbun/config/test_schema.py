"""Tests for Neo4j schema installation."""

import asyncio
from io import StringIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j.exceptions import ClientError
from neomodel import adb
from pytest_mock import MockerFixture

from tanbun.config.database import DeadlineDriver
from tanbun.config.schema import ASYNC_LABELS, SchemaDatabase, install_schema
from tanbun.conftest import mark_async_test
from tanbun.feature.gamification.label import LResourceXpEvent


@mark_async_test()
async def test_install_schema_installs_all_labels(
    mocker: MockerFixture,
) -> None:
    """Install every explicitly declared label with its matching database API."""
    settings = mocker.patch("tanbun.config.schema.Settings").return_value
    settings.NEO4J_SCHEMA_TIMEOUT_SECONDS = 300
    install_async = mocker.patch(
        "tanbun.config.schema.SchemaDatabase.install_labels",
        new_callable=AsyncMock,
    )
    budgets = []

    def capture_budget(*args, **kwargs):
        budgets.append(DeadlineDriver(MagicMock()).session().budget)

    install_async.side_effect = capture_budget
    close_async = mocker.patch(
        "tanbun.config.schema.SchemaDatabase.close_connection",
        new_callable=AsyncMock,
    )

    await install_schema()

    settings.setup_db.assert_called_once_with()
    assert [call.args[0] for call in install_async.call_args_list] == list(ASYNC_LABELS)
    assert all(
        b.timeout == settings.NEO4J_SCHEMA_TIMEOUT_SECONDS
        and b.operation == "schema-install"
        for b in budgets
    )
    assert DeadlineDriver(MagicMock()).session().budget.timeout == 30  # noqa: PLR2004
    close_async.assert_awaited_once_with()


@mark_async_test()
async def test_schema_can_be_installed_twice() -> None:
    """実DBでも同じスキーマを繰り返し登録できる."""
    await install_schema()
    await install_schema()
    rows, _ = await adb.cypher_query(
        "SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties "
        "WHERE labelsOrTypes = ['ResourceXpEvent'] AND properties = ['key'] "
        "RETURN type",
    )
    assert rows == [["UNIQUENESS"]]


@mark_async_test()
async def test_concurrent_constraint_installation() -> None:
    """起動が重なった場合も同じ一意制約を安全に作れる."""
    await adb.cypher_query(
        "DROP CONSTRAINT constraint_unique_ResourceXpEvent_key IF EXISTS",
    )
    first, second = SchemaDatabase(), SchemaDatabase()
    try:
        await asyncio.gather(
            *(
                database._create_node_constraint(  # noqa: SLF001
                    LResourceXpEvent,
                    "key",
                    StringIO(),
                    quiet=True,
                )
                for database in (first, second)
            ),
        )
    finally:
        await first.close_connection()
        await second.close_connection()


@mark_async_test()
@pytest.mark.parametrize("count", [0, 1])
async def test_constraint_verifies_semantics(mocker: MockerFixture, count: int) -> None:
    """同名の別制約をスキップしても成功扱いにはしない."""
    query = mocker.patch.object(
        SchemaDatabase,
        "cypher_query",
        new_callable=AsyncMock,
        side_effect=[([], []), ([[count]], [])],
    )
    database = SchemaDatabase()
    if count:
        await database._create_node_constraint(  # noqa: SLF001
            LResourceXpEvent,
            "key",
            StringIO(),
            quiet=True,
        )
    else:
        with pytest.raises(RuntimeError, match="uniqueness constraint is missing"):
            await database._create_node_constraint(  # noqa: SLF001
                LResourceXpEvent,
                "key",
                StringIO(),
                quiet=True,
            )
    assert "IF NOT EXISTS" in query.call_args_list[0].args[0]


@mark_async_test()
async def test_collision_is_not_silently_ignored(mocker: MockerFixture) -> None:
    """残存インデックスは削除せず診断を添えて停止する."""
    error = ClientError._hydrate_neo4j(  # noqa: SLF001
        code="Neo.ClientError.Schema.IndexWithNameAlreadyExists",
        message="existing index",
    )
    query = mocker.patch.object(
        SchemaDatabase,
        "cypher_query",
        new_callable=AsyncMock,
        side_effect=error,
    )
    with pytest.raises(RuntimeError, match="No index was deleted automatically"):
        await SchemaDatabase()._create_node_constraint(  # noqa: SLF001
            LResourceXpEvent,
            "key",
            StringIO(),
            quiet=True,
        )
    query.assert_awaited_once()


@mark_async_test()
async def test_actual_index_collision_preserves_index() -> None:
    """一時DBに同名インデックスを置き、制約と誤認・削除しないことを確認."""
    name = "constraint_unique_ResourceXpEvent_key"
    await adb.cypher_query(f"DROP CONSTRAINT `{name}` IF EXISTS")
    await adb.cypher_query(
        f"CREATE INDEX `{name}` FOR (n:ResourceXpEvent) ON (n.key)",
    )
    database = SchemaDatabase()
    try:
        with pytest.raises(RuntimeError):
            await database._create_node_constraint(  # noqa: SLF001
                LResourceXpEvent,
                "key",
                StringIO(),
                quiet=True,
            )
        rows, _ = await adb.cypher_query(
            "SHOW INDEXES YIELD name, owningConstraint "
            "WHERE name = $name RETURN owningConstraint",
            {"name": name},
        )
        assert rows == [[None]]
    finally:
        await adb.cypher_query(f"DROP INDEX `{name}` IF EXISTS")
        await database._create_node_constraint(  # noqa: SLF001
            LResourceXpEvent,
            "key",
            StringIO(),
            quiet=True,
        )
        await database.close_connection()


@mark_async_test()
async def test_schema_failure_closes_connection(mocker: MockerFixture) -> None:
    """制約登録失敗時も接続を残さない."""
    settings = mocker.patch("tanbun.config.schema.Settings").return_value
    settings.NEO4J_SCHEMA_TIMEOUT_SECONDS = 300
    mocker.patch.object(
        SchemaDatabase,
        "install_labels",
        new_callable=AsyncMock,
        side_effect=RuntimeError("schema failure"),
    )
    close = mocker.patch.object(
        SchemaDatabase,
        "close_connection",
        new_callable=AsyncMock,
    )
    with pytest.raises(RuntimeError, match="schema failure"):
        await install_schema()
    close.assert_awaited_once_with()
