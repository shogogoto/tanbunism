"""開放キーのトランザクション再試行を、競合の発生確率に頼らず検証する."""
# ruff: noqa: PLR2004, SLF001

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from neo4j.exceptions import TransientError

from . import dungeon_keys
from .inventory import DungeonKey
from .state import GameState


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """DBの自動消去を置き換え、各段階の競合を確定的に発生させる."""
    monkeypatch.setattr(dungeon_keys, "adb", SimpleNamespace(_active_transaction=None))
    monkeypatch.setattr(dungeon_keys, "atomic", nullcontext)
    monkeypatch.setattr(
        dungeon_keys,
        "owned_resource_name",
        AsyncMock(return_value="学びの本"),
    )
    monkeypatch.setattr(dungeon_keys, "read_state", AsyncMock(return_value=GameState()))
    monkeypatch.setattr(dungeon_keys, "sleep", AsyncMock())
    monkeypatch.setattr(
        dungeon_keys,
        "create_notification",
        AsyncMock(return_value="notification"),
    )
    monkeypatch.setattr(dungeon_keys, "dispatch_saved_notifications", AsyncMock())


def deadlock():
    """Neo4jが返すものと同じコードのエラー."""
    return TransientError._hydrate_neo4j(
        code="Neo.TransientError.Transaction.DeadlockDetected",
        message="deadlock",
    )


@pytest.mark.asyncio
async def test_deadlock_rereads_state_and_dispatches_only_successful_notification(
    monkeypatch,
):
    """CASをやり直しても、所持品と通知送信は一回だけになる."""
    resource = uuid4().hex
    save = AsyncMock(side_effect=[deadlock(), [["user"]]])
    monkeypatch.setattr(dungeon_keys, "compare_and_save", save)
    result = await dungeon_keys.grant_dungeon_key("user", resource)
    assert result.revision == 1
    assert result.save.economy.inventory == {f"@dungeon:{resource}": 1}
    assert dungeon_keys.read_state.await_count == 2
    assert save.await_count == 2
    dungeon_keys.create_notification.assert_awaited_once()
    dungeon_keys.dispatch_saved_notifications.assert_awaited_once_with(
        "user",
        ["notification"],
    )
    dungeon_keys.sleep.assert_awaited_once_with(0.05)


@pytest.mark.asyncio
async def test_retry_observes_another_winner_without_creating_another_key(monkeypatch):
    """再読込した状態に配布履歴があれば、通知も増やさない."""
    resource = uuid4().hex
    identity = f"@dungeon:{resource}"
    winner = GameState(revision=1)
    winner.save.economy.dungeon_keys[identity] = DungeonKey(
        resource_id=resource,
        name="学びの本",
    )
    winner.save.economy.inventory[identity] = 1
    dungeon_keys.read_state.side_effect = [GameState(), winner]
    save = AsyncMock(side_effect=deadlock())
    monkeypatch.setattr(dungeon_keys, "compare_and_save", save)
    result = await dungeon_keys.grant_dungeon_key("user", resource)
    assert result == winner
    save.assert_awaited_once()
    dungeon_keys.create_notification.assert_not_awaited()
    dungeon_keys.dispatch_saved_notifications.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [True, False])
async def test_does_not_retry_a_failed_outer_transaction_or_other_errors(
    monkeypatch,
    outer,
):
    """外側のimportを部分確定させず、デッドロック以外も握りつぶさない."""
    if outer:
        dungeon_keys.adb._active_transaction = object()
    error = (
        deadlock()
        if outer
        else TransientError._hydrate_neo4j(
            code="Neo.TransientError.Transaction.TransactionTimedOut",
            message="timeout",
        )
    )
    save = AsyncMock(side_effect=error)
    monkeypatch.setattr(dungeon_keys, "compare_and_save", save)
    with pytest.raises(TransientError):
        await dungeon_keys.grant_dungeon_key("user", uuid4().hex)
    save.assert_awaited_once()
    dungeon_keys.sleep.assert_not_awaited()
    dungeon_keys.dispatch_saved_notifications.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_is_bounded_and_returns_conflict_when_exhausted(monkeypatch):
    """最大3回。最後の失敗後には無駄な待機や通知を行わない."""
    save = AsyncMock(side_effect=deadlock())
    monkeypatch.setattr(dungeon_keys, "compare_and_save", save)
    with pytest.raises(HTTPException) as conflict:
        await dungeon_keys.grant_dungeon_key("user", uuid4().hex)
    assert conflict.value.status_code == 409
    assert save.await_count == 3
    assert dungeon_keys.sleep.await_count == 2
    dungeon_keys.create_notification.assert_not_awaited()
    dungeon_keys.dispatch_saved_notifications.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_deadlock_retries_without_dispatching_failed_attempt(
    monkeypatch,
):
    """通知の保存でデッドロックしても、破棄した試行は配信しない."""
    monkeypatch.setattr(
        dungeon_keys,
        "compare_and_save",
        AsyncMock(return_value=[["user"]]),
    )
    dungeon_keys.create_notification.side_effect = [deadlock(), "notification"]
    result = await dungeon_keys.grant_dungeon_key("user", uuid4().hex)
    assert result.revision == 1
    assert dungeon_keys.create_notification.await_count == 2
    dungeon_keys.dispatch_saved_notifications.assert_awaited_once_with(
        "user",
        ["notification"],
    )
