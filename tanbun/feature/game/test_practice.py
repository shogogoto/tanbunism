"""Practice reuses combat but never writes player state, answers or XP."""
# ruff: noqa: PLR2004

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from . import combat, practice
from .admin import EnemyBalanceSimulationRequest, simulate_enemy_balance
from .balance import Allocation, GameBalance
from .combat import CombatContext, TurnRequest, derive_enemies
from .state import GameSave, GameState, Run


def source_state(resource):
    """Two real-shaped readable quizzes in an isolated dungeon snapshot."""
    quizzes = [
        {
            "quiz_id": str(uuid4()),
            "quiz_type": "sent2term",
            "prompt": {"subject": f"問題{i}", "answer_kind": "term"},
            "statement": "説明",
            "options": {"yes": "正解", "no": "誤答"},
            "correct": ["yes"],
            "no_correct_option": False,
            "created": "2026-10-12T00:00:00Z",
        }
        for i in range(5)
    ]
    return GameState(
        save=GameSave(
            run=Run(
                resourceId=str(resource),
                name="本",
                hp=20,
                maxHp=35,
                attack=10,
                defense=1,
                moves=3,
                kills=2,
                enemyHp=0,
                enemyMaxHp=1,
                quizCursor=0,
                readIds=[],
                phase="path",
            ),
            content={
                "knowledge": [],
                "quizzes": quizzes,
                "regionQuizPools": {"0": [q["quiz_id"] for q in quizzes]},
            },
            allocation=Allocation(hp=1),
        ),
    )


def test_practice_uses_enemy_snapshot_and_draft_initial_player_values(monkeypatch):
    """Enemy trials stay sealed; draft player values require no saved user/dungeon."""
    snapshot = simulate_enemy_balance(
        EnemyBalanceSimulationRequest(balance=GameBalance(), power=250, achievement=3),
    )
    draft = GameBalance(
        base_hp=81,
        base_attack=23,
        base_defense=7,
        base_seconds=12,
        enemy_hp=999,
        enemy_attack=888,
        min_enemies=2,
        max_encounter_enemies=2,
        encounter_growth_rate=0,
    )
    reads = AsyncMock(side_effect=AssertionError("Simulation must not read users"))
    writes = AsyncMock(side_effect=AssertionError("Simulation must not persist"))
    monkeypatch.setattr(practice, "read_state", reads)
    monkeypatch.setattr(combat, "persist", writes)
    request = practice.SimulatedPracticeRequest(simulation=snapshot, balance=draft)
    session = practice.prepare_simulated_practice(request)
    assert session.state.save.allocation == Allocation()
    assert session.state.save.run.hp == 81
    assert session.state.save.run.attack == 23
    assert session.state.save.run.defense == 7
    assert session.state.save.run.answerSeconds == 12
    assert len(session.context.enemies) == 2
    for enemy in session.context.enemies:
        source = snapshot.enemies[int(enemy.name.rsplit(" ", 1)[1]) - 1]
        assert (enemy.hp, enemy.attack) == (source.hp, source.attack)
        assert len(enemy.quizIndexes) == source.quiz_count
    second = practice.prepare_simulated_practice(request)
    assert session.context == second.context
    marker = session.state.save.battle
    resolved = practice.practice_turn(
        practice.PracticeTurn(
            session=session,
            turn=TurnRequest(
                battle_id=marker.id,
                turn=marker.turn,
                answers={enemy: ["yes"] for enemy in marker.enemies},
            ),
        ),
    )
    assert all(resolved["results"].values())
    assert resolved["damage"] == 0
    reads.assert_not_awaited()
    writes.assert_not_awaited()


def test_simulated_practice_adds_points_to_unsaved_initial_values():
    """Player points are an explicit disposable override for an enemy snapshot."""
    balance = GameBalance(base_hp=60, base_attack=17, base_seconds=30)
    snapshot = simulate_enemy_balance(
        EnemyBalanceSimulationRequest(balance=balance, power=100, achievement=1),
    )
    session = practice.prepare_simulated_practice(
        practice.SimulatedPracticeRequest(
            simulation=snapshot,
            balance=balance,
            allocation=Allocation(hp=2, attack=9, defense=1, seconds=2),
        ),
    )
    assert session.state.save.run.hp == 70
    assert session.state.save.run.attack == 35
    assert session.state.save.run.defense == 2
    assert session.state.save.run.answerSeconds == 36


async def _prepared(monkeypatch):
    """Seed/settings recreate enemies; both source snapshot and DB remain untouched."""
    resource, user = uuid4(), uuid4()
    source = source_state(resource)
    original = source.model_dump_json()
    monkeypatch.setattr(practice, "read_state", AsyncMock(return_value=source))

    async def context(_user, state, *, balance):  # noqa: RUF029 - async test double
        ids = {q["quiz_id"].replace("-", ""): 0 for q in state.save.content["quizzes"]}
        return CombatContext(
            balance=balance,
            enemies=derive_enemies(state.save.content, ids, 100, balance),
        )

    monkeypatch.setattr(practice, "combat_context", context)
    writes = AsyncMock(side_effect=AssertionError("Practice must not persist"))
    monkeypatch.setattr(combat, "persist", writes)
    request = practice.PracticeRequest(
        user_id=user,
        resource_id=resource,
        level=1,
        seed=9,
        balance=GameBalance(min_enemies=2, max_encounter_enemies=2),
    )
    first = await practice.prepare_practice(request)
    second = await practice.prepare_practice(request)
    assert first.context == second.context
    assert first.state.save.battle == second.state.save.battle
    assert first.state.save.run.hp == 40
    assert source.model_dump_json() == original
    writes.assert_not_awaited()
    return first


@mark_async_test()
async def test_prepare_replays_draft_and_does_not_mutate_source(monkeypatch):
    """Preparation only creates an isolated, reproducible snapshot."""
    await _prepared(monkeypatch)


@mark_async_test()
async def test_dungeon_practice_keeps_saved_enemy_identity_and_quiz_sets(monkeypatch):
    """Keep actual roster IDs and remap only the selected region's quizzes."""
    await _prepared(monkeypatch)
    resource = uuid4()
    source = source_state(resource)
    quizzes = source.save.content["quizzes"]
    source.save.content["regionQuizPools"]["0"] = [
        quizzes[i]["quiz_id"] for i in [1, 3]
    ]
    source.save.content["regionEnemies"] = {
        "0": [
            {
                "id": "saved-enemy",
                "name": "保存済みの敵",
                "quizIndex": 1,
                "quizIndexes": [1, 3],
            },
        ],
    }
    original = source.model_dump_json()
    monkeypatch.setattr(practice, "read_state", AsyncMock(return_value=source))
    session = await practice.prepare_practice(
        practice.PracticeRequest(
            user_id=uuid4(),
            resource_id=resource,
            level=1,
            balance=GameBalance(min_enemies=1, max_encounter_enemies=1),
            allocation=Allocation(),
        ),
    )
    assert len(session.context.enemies) == 1
    enemy = session.context.enemies[0]
    assert enemy.id == "saved-enemy"
    assert enemy.name == "保存済みの敵"
    assert enemy.quizIndexes == [0, 1]
    assert source.model_dump_json() == original


@mark_async_test()
async def test_prepare_uses_selected_players_allocation(monkeypatch):
    """Player selection is independent of dungeon ownership and never changes either."""
    await _prepared(monkeypatch)
    resource, owner, player = uuid4(), uuid4(), uuid4()
    source = source_state(resource)
    player_state = GameState(save=GameSave(allocation=Allocation(attack=8)))
    originals = (source.model_dump_json(), player_state.model_dump_json())
    read = AsyncMock(side_effect=[source, player_state])
    monkeypatch.setattr(practice, "read_state", read)
    session = await practice.prepare_practice(
        practice.PracticeRequest(
            user_id=owner,
            player_user_id=player,
            resource_id=resource,
            level=1,
            balance=GameBalance(),
        ),
    )
    assert session.state.save.allocation == player_state.save.allocation
    assert session.state.save.run.attack == 26
    assert session.state.save.run.maxHp == 35
    assert [call.args[0] for call in read.await_args_list] == [owner, player]
    assert (source.model_dump_json(), player_state.model_dump_json()) == originals


@mark_async_test()
async def test_turn_correctness_timeout_next_and_replay(monkeypatch):
    """Correctness and unanswered damage use live rules without writes."""
    session = await _prepared(monkeypatch)
    original = session.model_dump_json()
    marker = session.state.save.battle
    first_enemy = marker.enemies[0]
    turn = TurnRequest(battle_id=marker.id, turn=0, answers={first_enemy: ["yes"]})
    result = practice.practice_turn(practice.PracticeTurn(session=session, turn=turn))
    assert result["results"] == {first_enemy: True}
    assert result["damage"] > 0
    assert result["state"].save.run.hp < session.state.save.run.hp
    assert session.model_dump_json() == original
    assert (
        practice.practice_turn(practice.PracticeTurn(session=session, turn=turn))
        == result
    )
    session.state = result["state"]
    next_state = practice.next_practice_turn(session)
    assert next_state.save.battle.turn == 1
    assert next_state.save.run.answerDeadline is not None
    assert next_state.save.battleFeedback is None


@mark_async_test()
async def test_retreat_and_simultaneous_death(monkeypatch):
    """Practice preserves live loss precedence and returns only disposable state."""
    session = await _prepared(monkeypatch)
    session.state.save.run.hp = 1
    marker = session.state.save.battle
    result = practice.practice_turn(
        practice.PracticeTurn(
            session=session,
            turn=TurnRequest(battle_id=marker.id, turn=0, retreat=True),
        ),
    )
    assert result["state"].save.run.phase == "defeated"
    assert result["state"].save.battle is None
    with pytest.raises(HTTPException):
        practice.practice_turn(
            practice.PracticeTurn(
                session=session,
                turn=TurnRequest(battle_id="wrong", turn=0),
            ),
        )


@mark_async_test()
async def test_practice_routes_require_admin(ac, monkeypatch):
    """Anonymous and ordinary users cannot read admin simulation data."""
    user = await aregister("practice@example.com")
    headers = await aauth_header(user.email)
    body = {
        "user_id": str(user.uid),
        "resource_id": str(uuid4()),
        "level": 1,
        "balance": GameBalance().model_dump(),
    }
    path = "/admin/settings/game-balance/practice"
    allocation_path = f"/admin/users/{user.uid}/game/allocation"
    assert (await ac.get(allocation_path)).status_code == 401
    assert (await ac.get(allocation_path, headers=headers)).status_code == 403
    assert (await ac.post(path, json=body)).status_code == 401
    assert (await ac.post(path, json=body, headers=headers)).status_code == 403
    session = await _prepared(monkeypatch)
    session_body = session.model_dump(mode="json")
    turn = {
        "battle_id": session.state.save.battle.id,
        "turn": 0,
        "answers": {},
        "defeated": [],
        "retreat": True,
    }
    simulated_body = practice.SimulatedPracticeRequest(
        simulation=simulate_enemy_balance(
            EnemyBalanceSimulationRequest(
                balance=GameBalance(),
                power=100,
                achievement=1,
            ),
        ),
        balance=GameBalance(base_hp=81),
    ).model_dump(mode="json")
    for endpoint, payload in [
        ("turn", {"session": session_body, "turn": turn}),
        ("next", session_body),
        ("simulation", simulated_body),
    ]:
        assert (await ac.post(f"{path}/{endpoint}", json=payload)).status_code == 401
        assert (
            await ac.post(f"{path}/{endpoint}", json=payload, headers=headers)
        ).status_code == 403
    user.is_superuser = True
    await user.save()
    response = await ac.post(
        f"{path}/turn",
        json={"session": session_body, "turn": turn},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["state"]["save"]["battle"] is None
    response = await ac.post(f"{path}/simulation", json=simulated_body, headers=headers)
    assert response.status_code == 200
    assert response.json()["state"]["save"]["run"]["hp"] == 81
    response = await ac.get(allocation_path, headers=headers)
    assert response.status_code == 200
    assert response.json() == Allocation().model_dump()
