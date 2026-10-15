"""復習報酬の独立トランザクションだけを安全に再試行する."""
# ruff: noqa: PLR2004, SLF001

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from neo4j.exceptions import TransientError

from tanbun.feature.domain.datetime import TZ

from . import economy
from .inventory import Economy
from .state import GameSave, GameState


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """Avoid live database use and replace the global DB cleanup fixture."""
    initial = GameState()
    monkeypatch.setattr(economy, "adb", SimpleNamespace(_active_transaction=None))
    monkeypatch.setattr(economy, "atomic", nullcontext)
    monkeypatch.setattr(economy, "read_state", AsyncMock(return_value=initial))
    monkeypatch.setattr(economy, "sleep", AsyncMock())
    monkeypatch.setattr(
        economy,
        "quiz_review_progress",
        AsyncMock(
            return_value={
                "day": datetime.now(TZ).date().isoformat(),
                "done": 20,
                "total": 20,
            },
        ),
    )


def deadlock():
    """Construct the same transient error reported by Neo4j."""
    return TransientError._hydrate_neo4j(
        code="Neo.TransientError.Transaction.DeadlockDetected",
        message="deadlock",
    )


@pytest.mark.asyncio
async def test_retry_deadlock_from_fresh_state_without_duplicate_potion(monkeypatch):
    """ロールバック後に読み直し、一度だけ付与する."""
    save = AsyncMock(side_effect=[deadlock(), [["user"]]])
    monkeypatch.setattr(economy, "compare_and_save", save)
    result, awarded = await economy.grant_review_reward("user", quiz=True)
    assert awarded
    assert result.save.economy.inventory == {"step_potion": 1}
    assert save.await_count == 2
    assert economy.read_state.await_count == 2
    economy.sleep.assert_awaited_once_with(0.05)


@pytest.mark.asyncio
async def test_retry_respects_another_winner_receipt(monkeypatch):
    """競合相手が付与した日付を読み直したら、報酬を増やさない."""
    winner = GameState(
        revision=1,
        save=GameSave(
            economy=Economy(
                inventory={"step_potion": 1},
                quiz_reward_day=datetime.now(TZ).date().isoformat(),
            ),
        ),
    )
    economy.read_state.side_effect = [GameState(), winner]
    save = AsyncMock(side_effect=deadlock())
    monkeypatch.setattr(economy, "compare_and_save", save)
    result, awarded = await economy.grant_review_reward("user", quiz=True)
    assert not awarded
    assert result == winner
    save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [True, False])
async def test_no_retry_inside_outer_transaction_or_for_other_errors(
    monkeypatch,
    outer,
):
    """閉じたHTTPトランザクションを再利用せず、タイムアウトも再試行しない."""
    if outer:
        economy.adb._active_transaction = object()
    error = (
        deadlock()
        if outer
        else TransientError._hydrate_neo4j(
            code="Neo.TransientError.Transaction.TransactionTimedOut",
            message="timeout",
        )
    )
    save = AsyncMock(side_effect=error)
    monkeypatch.setattr(economy, "compare_and_save", save)
    with pytest.raises(TransientError):
        await economy.grant_review_reward("user", quiz=True)
    save.assert_awaited_once()
    economy.sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_deadlock_retry_is_bounded(monkeypatch):
    """3回を超えて待たず、次回の状態読み込みで付与を再確認する."""
    save = AsyncMock(side_effect=deadlock())
    monkeypatch.setattr(economy, "compare_and_save", save)
    result, awarded = await economy.grant_review_reward("user", quiz=True)
    assert not awarded
    assert result.save.economy.inventory == {}
    assert save.await_count == 3
