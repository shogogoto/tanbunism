"""戦闘開始・一括回答・再送・撤退・育成ポイントの回帰テスト."""
# ruff: noqa: PLR2004

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser

from . import combat
from .balance import Allocation, GameBalance, update_game_balance
from .combat import EncounterRequest, TurnRequest
from .state import GameSave, GameState, Run, StateUpdate, read_state, write_state

u = async_fixture()(fx_u)


async def encounter(user: LUser) -> GameState:
    """本物の出題・リソースから二体の敵を準備する."""
    await update_game_balance(GameBalance(min_enemies=2, max_encounter_enemies=2))
    target = await LSentence.nodes.first(val="ccc")
    quizzes = [
        (
            await generate_quiz(kind, CandidateType.NEAR, target.uid, 4, user.uid)
        ).to_readable()
        for kind in [QuizType.SENT2TERM, QuizType.TERM2SENT]
    ]
    [growth] = await fetch_resource_growth(user.uid)
    save = GameSave(
        run=Run(
            resourceId=str(growth.resource_id),
            name="本",
            hp=35,
            maxHp=35,
            attack=10,
            defense=1,
            moves=1,
            kills=0,
            enemyHp=0,
            enemyMaxHp=1,
            quizCursor=0,
            readIds=[str(target.uid)],
            phase="battle",
        ),
        maps={
            str(growth.resource_id): {
                "current": str(target.uid),
                "places": [{"id": str(target.uid), "region": 2}],
                "edges": [],
            },
        },
        content={
            "quizzes": [q.model_dump(mode="json") for q in quizzes],
            "regionEnemies": {
                "2": [
                    {
                        "id": f"enemy{i}",
                        "name": f"敵{i}",
                        "quizIndex": i,
                        "hp": 99999,
                        "attack": 99999,
                    }
                    for i in range(2)
                ],
            },
        },
    )
    state = await write_state(user.uid, StateUpdate(revision=0, save=save))
    return await combat.start_combat(
        user.uid,
        EncounterRequest(revision=state.revision, region=2),
    )


def answers(state: GameState) -> dict[str, list[str]]:
    """固定クイズの実際の正解を使う."""
    return {
        enemy_id: state.save.content["quizzes"][quiz_index]["correct"]
        for enemy_id, quiz_index in state.save.battle.quizIndices.items()
    }


@mark_async_test()
async def test_batch_answer_receipt_and_live_enemy_stats(u: LUser):
    """旧能力値を無視し、全問正解の精算と再送で回答・XPを増やさない."""
    state = await encounter(u)
    marker = state.save.battle
    assert len(marker.enemies) == len(set(marker.enemies)) == 2
    assert state.save.run.enemyHp == 0
    context = await combat.combat_context(u.uid)
    before = context.enemies[0].hp
    assert before < 99999
    await update_game_balance(GameBalance(power_hp=2))
    changed = await combat.combat_context(u.uid)
    assert changed.enemies[0].hp > before
    body = TurnRequest(
        battle_id=marker.id,
        turn=0,
        answers=answers(state),
        defeated=marker.enemies,
    )
    result = await combat.settle_turn(u.uid, body)
    assert result["damage"] == 0
    assert all(result["results"].values())
    saved = await read_state(u.uid)
    assert saved.save.battle is None
    assert saved.save.run.kills == 2
    assert saved.save.run.hp == 35
    rows, _ = await adb.cypher_query("MATCH (a:Answer) RETURN count(a)")
    assert rows[0][0] == 2
    [growth] = await fetch_resource_growth(u.uid)
    assert growth.total_xp > 0
    retry = await combat.settle_turn(u.uid, body)
    assert retry == result
    assert (await fetch_resource_growth(u.uid))[0].total_xp == growth.total_xp
    assert (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][0] == 2


@mark_async_test()
async def test_unanswered_retreat_is_once_and_keeps_discovery(u: LUser):
    """再読み込みは全問未回答のダメージだけ。履歴/XPなし、歩数/開拓は維持."""
    await encounter(u)
    context = await combat.combat_context(u.uid)
    damage = sum(max(1, e.attack - 1) for e in context.enemies)
    saved = await combat.abandon_combat(u.uid)
    assert saved.save.battle is None
    assert saved.save.run.hp == max(0, 35 - damage)
    assert saved.save.run.moves == 1
    dungeon = saved.save.maps[saved.save.run.resourceId]
    assert dungeon.current == "@entrance"
    assert len(dungeon.places) == 1
    assert await combat.abandon_combat(u.uid) == saved
    assert (await fetch_resource_growth(u.uid))[0].total_xp == 0
    assert (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][0] == 0


@mark_async_test()
async def test_confirmed_retreat_records_only_actual_answers(u: LUser):
    """明示的な撤退では確定済みの一問だけ履歴に残す."""
    state = await encounter(u)
    selected = answers(state)
    first = next(iter(selected))
    body = TurnRequest(
        battle_id=state.save.battle.id,
        turn=0,
        answers={first: selected[first]},
        retreat=True,
    )
    result = await combat.settle_turn(u.uid, body)
    assert result["damage"] > 0
    assert result["results"] == {first: True}
    assert result["state"]["save"]["battle"] is None
    assert (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][0] == 1


@mark_async_test()
async def test_turn_rollback_and_combat_blocks_allocation(u: LUser, monkeypatch):
    """回答保存が失敗すると状態もrollback。戦闘中は振り直し不可."""
    state = await encounter(u)
    with pytest.raises(HTTPException) as blocked:
        await combat.set_allocation(u.uid, Allocation())
    assert blocked.value.status_code == 409
    monkeypatch.setattr(
        combat,
        "create_answer_in_transaction",
        AsyncMock(side_effect=RuntimeError("save failed")),
    )
    with pytest.raises(RuntimeError):
        await combat.settle_turn(
            u.uid,
            TurnRequest(battle_id=state.save.battle.id, turn=0, answers=answers(state)),
        )
    assert await read_state(u.uid) == state
    assert (await fetch_resource_growth(u.uid))[0].total_xp == 0


@mark_async_test()
async def test_allocation_budget_reset_and_no_healing(u: LUser):
    """Lv2の3ptを割り振り、HP上昇でも回復させず無料リセットできる."""
    state = await encounter(u)
    await combat.settle_turn(
        u.uid,
        TurnRequest(
            battle_id=state.save.battle.id,
            turn=0,
            answers=answers(state),
            retreat=True,
        ),
    )
    uid = str(u.uid).replace("-", "")
    await adb.cypher_query(
        """CREATE (:ResourceXpEvent {key:'allocation-test', user_id:$uid,
            source:'quiz_answer', xp:10})""",
        {"uid": uid},
    )
    before = await read_state(u.uid)
    assigned = await combat.set_allocation(u.uid, Allocation(hp=3))
    assert assigned.save.run.maxHp == 50
    assert assigned.save.run.hp == before.save.run.hp
    with pytest.raises(HTTPException) as invalid:
        await combat.set_allocation(u.uid, Allocation(hp=4))
    assert invalid.value.status_code == 422
    reset = await combat.set_allocation(u.uid, Allocation())
    assert reset.save.run.hp == before.save.run.hp
    assert reset.save.run.maxHp == 35


def test_simultaneous_death_loses_and_no_auto_level_stats():
    """撃破条件と同時にHP0になっても攻略扱いにしない."""
    state = GameState(
        save=GameSave(
            run=Run(
                resourceId="book",
                name="本",
                hp=1,
                maxHp=35,
                attack=10,
                defense=1,
                moves=5,
                kills=2,
                enemyHp=0,
                enemyMaxHp=1,
                quizCursor=0,
                readIds=[],
                phase="battle",
            ),
            battle={"id": "battle", "region": 0, "enemies": ["a", "b"]},
        ),
    )
    result = combat.apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0, defeated=["a"]),
        {"a": True},
        1,
        Allocation().stats(GameBalance()),
    )
    assert result.save.run.phase == "defeated"
    assert result.save.clears == {}
