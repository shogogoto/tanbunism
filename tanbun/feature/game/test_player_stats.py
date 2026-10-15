"""保存された能力値ではなく、現在の設定と育成・装備を正本にする."""
# ruff: noqa: PLR2004

import json
from uuid import uuid4

import pytest
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.user.label import LUser

from .admin import list_game_dungeons
from .balance import Allocation, GameBalance, update_game_balance
from .inventory import Economy
from .state import (
    DERIVED_PLAYER_FIELDS,
    GameSave,
    GameState,
    Run,
    StateUpdate,
    read_state,
    write_state,
)


@mark_async_test()
@pytest.mark.parametrize("legacy", [True, False])
async def test_existing_player_uses_current_balance_and_saves_only_sources(*, legacy):
    """旧・新形式、休止中、装備、期限を保持し、古い端末の能力値も採用しない."""
    user = await LUser(email="derived-stats@example.com").save()
    other = str(uuid4())
    run = Run(
        resourceId=str(uuid4()),
        name="本",
        hp=30,
        maxHp=999,
        mp=8,
        maxMp=999,
        attack=999,
        defense=999,
        moves=2,
        kills=1,
        enemyHp=10,
        enemyMaxHp=20,
        quizCursor=0,
        readIds=["a"],
        phase="path",
        answerSeconds=45,
        answerDeadline=123456789,
    )
    state = GameState(
        revision=4,
        save=GameSave(
            allocation=Allocation(hp=2, mp=1, attack=3, defense=4, seconds=5),
            economy=Economy(inventory={"bronze_sword": 1}, weapon="bronze_sword"),
            run=run,
            dungeons={
                other: {
                    "run": run.model_copy(
                        update={
                            "resourceId": other,
                            "hp": 0,
                            "mp": 0,
                        },
                    ),
                },
            },
        ),
    )
    payload = state.model_dump()
    if not legacy:
        for saved_run in [
            payload["save"]["run"],
            payload["save"]["dungeons"][other]["run"],
        ]:
            for field in DERIVED_PLAYER_FIELDS:
                saved_run.pop(field)
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state, u.game_revision=4",
        {"uid": user.uid, "state": json.dumps(payload)},
    )
    balance = GameBalance(base_hp=50, base_mp=20, base_attack=20, base_defense=5)
    await update_game_balance(balance)
    restored = await read_state(user.uid)
    assert restored.revision == 4  # Reading doesn't increment revision or heal.
    current = restored.save.run
    assert (current.maxHp, current.maxMp, current.attack, current.defense) == (
        60,
        22,
        29,
        9,
    )
    assert (current.hp, current.mp, current.moves) == (30, 8, 2)
    assert (current.answerSeconds, current.answerDeadline) == (45, 123456789)
    parked = restored.save.dungeons[other].run
    assert (parked.maxHp, parked.attack, parked.hp, parked.mp) == (60, 29, 0, 0)
    # An old browser sends cached stats, but never owns the actual ability values.
    stale = restored.save.model_copy(deep=True)
    stale.run.attack = stale.run.defense = 999
    stale.allocation = Allocation(attack=999)
    stale.economy = Economy()
    saved = await write_state(user.uid, StateUpdate(revision=4, save=stale))
    assert saved.save.run.attack == 29
    assert saved.save.allocation == state.save.allocation
    assert saved.save.economy.weapon == "bronze_sword"
    rows, _ = await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) RETURN u.game_state",
        {"uid": user.uid},
    )
    stored = json.loads(rows[0][0])["save"]
    for saved_run in [stored["run"], stored["dungeons"][other]["run"]]:
        assert DERIVED_PLAYER_FIELDS.isdisjoint(saved_run)
    assert (await read_state(user.uid)) == saved
    # Admin's direct snapshot reader must also support the new storage format.
    assert len(await list_game_dungeons()) == 2
    await update_game_balance(GameBalance(base_hp=1, base_mp=0, hp_per_point=1))
    reduced = await read_state(user.uid)
    assert (reduced.save.run.hp, reduced.save.run.mp) == (3, 2)
    assert reduced.save.dungeons[other].run.hp == 0


@mark_async_test()
async def test_balance_refresh_does_not_restart_inflight_turn():
    """保存された秒数・期限は現在ターンの状態としてそのまま残す."""
    user = await LUser(email="turn-stats@example.com").save()
    state = GameState(
        save=GameSave(
            run=Run(
                resourceId="book",
                name="本",
                hp=20,
                maxHp=35,
                attack=10,
                defense=1,
                moves=1,
                kills=0,
                enemyHp=10,
                enemyMaxHp=20,
                quizCursor=0,
                readIds=[],
                phase="battle",
                answerSeconds=45,
                answerDeadline=123456789,
            ),
        ),
    )
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state",
        {"uid": user.uid, "state": state.model_dump_json()},
    )
    await update_game_balance(GameBalance(base_seconds=90, base_attack=25))
    run = (await read_state(user.uid)).save.run
    assert run.attack == 25
    assert (run.answerSeconds, run.answerDeadline) == (45, 123456789)
