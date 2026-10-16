"""自動行動の優先順位・消費・精算・保存権限."""
# ruff: noqa: PLR2004

import pytest
from fastapi import HTTPException
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.user.testing import aauth_header, aregister

from . import combat
from .balance import GameBalance
from .catalog import default_catalog
from .combat import EnemyStats, TurnRequest, apply_turn_result, style_enemies
from .gambits import GambitRule, choose_action, validate_rules
from .state import (
    BattleMarker,
    GameSave,
    GameState,
    Run,
    StateUpdate,
    read_state,
    write_state,
)

u = async_fixture()(fx_u)


def battle_state() -> GameState:
    """回復と攻撃の分岐を試せる最小の戦闘."""
    return GameState(
        save=GameSave(
            run=Run(
                resourceId="book",
                name="本",
                hp=10,
                maxHp=35,
                mp=10,
                maxMp=10,
                attack=10,
                defense=1,
                moves=1,
                kills=0,
                enemyHp=0,
                enemyMaxHp=1,
                quizCursor=0,
                readIds=[],
                phase="battle",
            ),
            battle=BattleMarker(id="battle", region=0, enemies=["a", "b"]),
        ),
    )


def test_priority_conditions_affordability_and_frozen_definition():
    """HP条件を優先し、MP不足なら所持品、それもなければ次へ進む."""
    state = battle_state()
    state.save.gambits = [
        GambitRule(condition="hp_below", threshold=30, definition_id="heal"),
        GambitRule(condition="enemy_count", threshold=2, definition_id="fire_all"),
        GambitRule(definition_id="fire"),
    ]
    catalog = default_catalog()
    action = choose_action(state.save, catalog)
    assert action.skill.id == "heal"
    catalog.skills[2].amount = 999
    assert action.skill.amount == 20
    state.save.run.hp = 35
    assert choose_action(state.save, catalog).skill.id == "fire_all"
    state.save.run.mp = 3
    assert choose_action(state.save, catalog).skill.id == "fire"
    state.save.run.mp = 0
    assert choose_action(state.save, catalog) is None
    state.save.run.hp = 10
    state.save.gambits.insert(
        0,
        GambitRule(action="item", definition_id="healing_potion"),
    )
    state.save.economy.inventory["healing_potion"] = 1
    assert choose_action(state.save, catalog).item_id == "healing_potion"


@pytest.mark.parametrize("condition", ["hp_below", "mp_below"])
def test_percentage_trigger_includes_exact_boundary(condition):
    """n%以下には、ちょうどn%も含まれる."""
    state = battle_state()
    run = state.save.run
    run.maxHp = run.maxMp = 100
    run.hp = run.mp = 30
    state.save.gambits = [
        GambitRule(condition=condition, threshold=30, definition_id="fire"),
    ]
    assert choose_action(state.save, default_catalog()) is not None
    run.hp = run.mp = 31
    assert choose_action(state.save, default_catalog()) is None


@pytest.mark.parametrize("correct", [True, False])
def test_recovery_uses_normal_answers_and_consumes_only_once_in_result(correct):
    """正解時だけ反撃前に回復し、未回答でも消費する。入力状態は変更しない."""
    state = battle_state()
    state.save.gambits = [GambitRule(definition_id="heal")]
    state.save.battle.gambitAction = choose_action(state.save, default_catalog())
    body = TurnRequest(
        battle_id="battle",
        turn=0,
        answers={"a": ["yes"]} if correct else {},
    )
    result = apply_turn_result(
        state,
        body,
        {"a": True} if correct else {},
        5,
        state.save.allocation.stats(GameBalance()),
    )
    assert result.save.run.hp == (25 if correct else 5)
    assert result.save.run.mp == 7
    assert state.save.run.mp == 10
    assert ("発動" if correct else "不発") in result.save.battleFeedback
    assert (
        apply_turn_result(
            state,
            body,
            {"a": True} if correct else {},
            5,
            state.save.allocation.stats(GameBalance()),
        )
        == result
    )


def test_items_do_not_use_mp_and_retreat_does_not_execute_gambits():
    """アイテムはMPと独立し、撤退時は効果・消費とも行わない."""
    state = battle_state()
    state.save.gambits = [GambitRule(action="item", definition_id="healing_potion")]
    state.save.economy.inventory["healing_potion"] = 1
    state.save.battle.gambitAction = choose_action(state.save, default_catalog())
    stats = state.save.allocation.stats(GameBalance())
    result = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0),
        {},
        0,
        stats,
    )
    assert result.save.economy.inventory["healing_potion"] == 0
    assert result.save.run.mp == 10
    retreat = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0, retreat=True),
        {},
        0,
        stats,
    )
    assert retreat.save.economy.inventory["healing_potion"] == 1
    for rule in (
        GambitRule(action="item", definition_id="missing"),
        GambitRule(definition_id="recover_steps"),
    ):
        with pytest.raises(HTTPException):
            validate_rules([rule], default_catalog())


def test_item_only_effects_and_locked_specialties_cannot_be_cast_directly():
    """消費なしの回復や、未解放の専門分野の発動を防ぐ."""
    state = battle_state()
    catalog = default_catalog()
    rule = GambitRule(definition_id="potion_heal")
    state.save.gambits = [rule]
    assert choose_action(state.save, catalog) is None
    with pytest.raises(HTTPException):
        validate_rules([rule], catalog)
    state.save.gambits = [GambitRule(definition_id="specialty_burst")]
    assert choose_action(state.save, catalog) is None
    state.save.bossClears = {"book": [5]}
    assert choose_action(state.save, catalog).skill.id == "specialty_burst"


def test_repeated_enemy_instances_share_configured_appearance_and_attack():
    """重複出現しても敵の素材は変わらず、固定クイズIDも変えない."""
    catalog = default_catalog()
    for enemy in catalog.enemies:
        enemy.icon = "cat"
        enemy.attack_skill_id = "fire"
    enemies = [
        EnemyStats(
            id=id_,
            name="敵",
            quizIndex=0,
            hp=20,
            attack=10,
            relations=0,
            region=0,
        )
        for id_ in ["a", "a#1"]
    ]
    result = style_enemies(enemies, catalog)
    assert result[0].icon == result[1].icon == "cat"
    assert result[0].attack == 15
    assert [e.id for e in result] == ["a", "a#1"]


@mark_async_test()
async def test_rules_are_revision_checked_and_legacy_snapshot_writes_preserve_them(ac):
    """他端末の設定を上書きせず、戦闘中の変更を拒否する."""
    user = await aregister("gambit@example.com")
    headers = await aauth_header(user.email)
    body = {"revision": 0, "rules": [{"definition_id": "fire"}]}
    assert (await ac.put("/game/gambits", json=body)).status_code == 401
    response = await ac.put("/game/gambits", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["save"]["gambits"][0]["definition_id"] == "fire"
    assert (
        await ac.put("/game/gambits", headers=headers, json=body)
    ).status_code == 409
    state = await read_state(user.uid)
    assert state.save.gambits[0].definition_id == "fire"
    restored = await write_state(
        user.uid,
        StateUpdate(revision=state.revision, save=GameSave()),
    )
    assert restored.save.gambits == state.save.gambits
    playing = battle_state()
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state",
        {"uid": user.uid, "state": playing.model_dump_json()},
    )
    assert (
        await ac.put("/game/gambits", headers=headers, json=body)
    ).status_code == 409


@mark_async_test()
async def test_receipt_retries_never_consume_gambit_twice(u):
    """実戦の再送は同じ精算を返し、MPと回答記録を増減しない."""
    from .test_combat import answers, encounter  # noqa: PLC0415

    state = await encounter(u)
    state.save.run.mp = 10
    state.save.run.maxMp = 10
    state.save.gambits = [GambitRule(definition_id="fire")]
    state.save.battle.gambitAction = choose_action(state.save, default_catalog())
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state",
        {"uid": u.uid, "state": state.model_dump_json()},
    )
    body = TurnRequest(battle_id=state.save.battle.id, turn=0, answers=answers(state))
    result = await combat.settle_turn(u.uid, body)
    assert result["state"]["save"]["run"]["mp"] == 7
    assert (await combat.settle_turn(u.uid, body))["state"]["save"]["run"]["mp"] == 7
