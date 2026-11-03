"""ボスは直近の領域だけでなく、その達成度までの全問を出題する."""

import operator
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from tanbun.conftest import mark_async_test

from . import boss
from .balance import GameBalance
from .combat import CombatContext, derive_boss
from .state import DungeonMap, MapPlace
from .test_practice import source_state


@pytest.mark.parametrize("level", [5, 10, 15, 20])
@mark_async_test()
async def test_boss_random_choice_uses_all_quizzes_through_its_level(
    level,
    monkeypatch,
):
    """古い領域を含む全問が抽選対象になり、後の達成度の問題は混ざらない."""
    user, resource = uuid4(), uuid4()
    state = source_state(resource)
    quizzes = [
        {**state.save.content["quizzes"][0], "quiz_id": uuid4().hex}
        for _ in range(level * 5 + 5)
    ]
    state.save.content["quizzes"] = quizzes
    state.save.maps[str(resource)] = DungeonMap(
        places=[MapPlace(id=uuid4().hex, region=i // 5) for i in range(level * 5)],
    )
    pool_ids = [quiz["quiz_id"] for quiz in quizzes[: level * 5]]
    pool = AsyncMock(return_value={"ready": True, "quiz_ids": pool_ids})
    validate = AsyncMock(return_value=True)
    restore = AsyncMock(return_value=[])
    choose = Mock(side_effect=operator.itemgetter(0))
    balance = GameBalance()

    def context(_user, updated):
        content = {**updated.save.content, "battle": updated.save.battle.model_dump()}
        enemy = derive_boss(content, dict.fromkeys(pool_ids), 100, balance)
        return CombatContext(balance=balance, enemies=[enemy])

    monkeypatch.setattr(boss, "read_state", AsyncMock(return_value=state))
    monkeypatch.setattr(boss, "get_or_prepare_region_pool", pool)
    monkeypatch.setattr(boss, "_validate_quiz_ids", validate)
    monkeypatch.setattr(boss, "restore_quiz_sources", restore)
    monkeypatch.setattr(boss, "combat_context", AsyncMock(side_effect=context))
    monkeypatch.setattr(boss, "prepare_action", AsyncMock())
    monkeypatch.setattr(boss, "persist", AsyncMock())
    monkeypatch.setattr(boss, "choice", choose)

    started = await boss.start_boss(
        user,
        boss.BossRequest(revision=state.revision, level=level),
    )

    pool.assert_awaited_once_with(user, str(resource), level)
    validate.assert_awaited_once_with(user, str(resource), pool_ids)
    assert started.save.battle.bossQuizIds == pool_ids
    choose.assert_called_once_with(list(range(level * 5)))
    assert list(started.save.battle.quizIndices.values()) == [0]
    restore.assert_awaited_once_with([])
    assert state.save.battle is None
