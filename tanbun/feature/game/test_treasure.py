"""宝箱の位置・開封履歴・未開封への設定反映。DBを使わずCAS境界を検証する."""
# ruff: noqa: PLR2004

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from . import state as state_module
from . import treasure
from .balance import GameBalance
from .inventory import ChestReward, Economy
from .state import DungeonMap, GameSave, GameState, MapPlace, Run, StateUpdate
from .treasure import ChestRequest, open_chest


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """Override the global destructive DB fixture with in-memory persistence."""
    places = [MapPlace(id=f"p{i}", region=i // 5) for i in range(10)]
    store = SimpleNamespace(
        state=GameState(
            save=GameSave(
                maps={"book": DungeonMap(current="p4", places=places)},
                run=Run(
                    resourceId="book",
                    name="本",
                    hp=10,
                    maxHp=35,
                    attack=10,
                    defense=1,
                    moves=5,
                    kills=0,
                    enemyHp=0,
                    enemyMaxHp=1,
                    quizCursor=0,
                    readIds=[p.id for p in places],
                    phase="rest",
                ),
            ),
        ),
    )

    def save(_user_id, update, state):
        if update.revision != store.state.revision:
            return []
        store.state = GameState.model_validate_json(
            state.model_dump_json(by_alias=True),
        )
        return [["user"]]

    monkeypatch.setattr(
        treasure,
        "read_state",
        AsyncMock(side_effect=lambda _: store.state),
    )
    monkeypatch.setattr(treasure, "compare_and_save", AsyncMock(side_effect=save))
    monkeypatch.setattr(treasure, "atomic", nullcontext)
    monkeypatch.setattr(
        treasure,
        "get_game_balance",
        AsyncMock(return_value=GameBalance()),
    )
    return store


@pytest.mark.asyncio
async def test_open_at_zero_moves_and_revisit_without_duplicate_reward(setup):
    """開封は歩数・知識の地点数を消費せず、再訪しても再取得できない."""
    first = await open_chest("user", ChestRequest(revision=0, region=0))
    assert first.save.economy.gold == 30
    assert first.save.economy.inventory == {"step_potion": 1}
    assert first.save.maps["book"].current == "@chest:0"
    assert len(first.save.maps["book"].places) == 10
    assert first.save.run.moves == 5
    assert first.save.run.phase == "rest"
    with pytest.raises(HTTPException) as stale:
        await open_chest("user", ChestRequest(revision=0, region=0))
    assert stale.value.status_code == 409
    second = await open_chest("user", ChestRequest(revision=1, region=0))
    assert second.save.economy == first.save.economy


@pytest.mark.asyncio
async def test_new_settings_apply_only_to_unopened_boxes(setup, monkeypatch):
    """全領域に設定を反映しても、開封済みの中身と取得数は変えない."""
    first = await open_chest("user", ChestRequest(revision=0, region=0))
    monkeypatch.setattr(
        treasure,
        "get_game_balance",
        AsyncMock(
            return_value=GameBalance(
                chest_gold=70,
                chest_healing_potions=2,
                chest_step_potions=0,
            ),
        ),
    )
    reopened = await open_chest("user", ChestRequest(revision=1, region=0))
    assert reopened.save.economy == first.save.economy
    setup.state.save.maps["book"].current = "p9"
    second = await open_chest("user", ChestRequest(revision=2, region=1))
    assert second.save.economy.gold == 100
    assert second.save.economy.inventory == {"step_potion": 1, "healing_potion": 2}
    assert second.save.economy.opened_chests["book"] == {
        "0": ChestReward(gold=30, step_potion=1),
        "1": ChestReward(gold=70, healing_potion=2),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("current", "region"), [("p0", 0), ("p4", 1), ("p9", 2)])
async def test_cannot_open_distant_or_incomplete_region(setup, current, region):
    """他領域や途中から開けられず、報酬・現在地は変わらない."""
    setup.state.save.maps["book"].current = current
    with pytest.raises(HTTPException) as error:
        await open_chest("user", ChestRequest(revision=0, region=region))
    assert error.value.status_code == 422
    treasure.compare_and_save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("phase", "hp"), [("battle", 10), ("defeated", 0)])
async def test_cannot_open_during_battle_or_defeat(setup, phase, hp):
    """戦闘中・敗北中に報酬を受け取らない."""
    setup.state.save.run.phase = phase
    setup.state.save.run.hp = hp
    with pytest.raises(HTTPException) as error:
        await open_chest("user", ChestRequest(revision=0, region=0))
    assert error.value.status_code == 409
    treasure.compare_and_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_update_does_not_return_uncommitted_rewards(monkeypatch):
    """二重クリックでCASに負けた応答は報酬取得を返さない."""
    monkeypatch.setattr(treasure, "compare_and_save", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException) as error:
        await open_chest("user", ChestRequest(revision=0, region=0))
    assert error.value.status_code == 409


def test_only_completed_region_treasure_can_be_current_location():
    """箱は保存できる現在地だが、単文や道の両端として扱わない."""
    places = [MapPlace(id=f"p{i}", region=0) for i in range(5)]
    assert DungeonMap(current="@chest:0", places=places).chest_ids == {"@chest:0"}
    with pytest.raises(ValidationError):
        DungeonMap(current="@chest:0", places=places[:4])
    with pytest.raises(ValidationError):
        DungeonMap(current="@chest:1", places=places)


@pytest.mark.asyncio
async def test_old_snapshot_cannot_reset_opened_chests_or_mint_rewards(monkeypatch):
    """旧クライアントが履歴を送らなくても開封・報酬を保持する."""
    opened = await open_chest("user", ChestRequest(revision=0, region=0))
    monkeypatch.setattr(state_module, "read_state", treasure.read_state)
    monkeypatch.setattr(state_module, "compare_and_save", treasure.compare_and_save)
    monkeypatch.setattr(
        state_module,
        "discover_dungeons",
        AsyncMock(return_value=False),
    )
    old_save = opened.save.model_copy(deep=True)
    old_save.economy = Economy(gold=9999)
    result = await state_module.write_state(
        "user",
        StateUpdate(revision=1, save=old_save),
    )
    assert result.save.economy == opened.save.economy
    assert result.save.maps["book"].current == "@chest:0"
