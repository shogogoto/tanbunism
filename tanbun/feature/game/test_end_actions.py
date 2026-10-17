"""通常回答とは独立する自動行動・配合・バブルの回帰テスト."""
# ruff: noqa: PLR2004

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.fixture import fx_u

from . import end_actions
from .balance import GameBalance
from .catalog import AilmentSkill, BuffSkill, RecoverySkill, default_catalog
from .combat import combat_context
from .effects import add_effect, sync_bubble
from .end_actions import (
    EndActionAnswer,
    EndActionRequest,
    answer_end_action,
    finish_action,
    start_end_action,
)
from .gambits import GambitRule
from .practice import PracticeAction, PracticeSession, practice_action
from .specialties import SpecialtyUpdate, strengthened, update_specialties
from .specialty_types import Specialty
from .state import StateUpdate, read_state, restore_state, write_state
from .test_gambits import battle_state
from .test_specialty import intermission, seed_state

u = async_fixture()(fx_u)


@mark_async_test()
@pytest.mark.parametrize("slots", [1, 2])
async def test_missing_specialty_source_skips_without_unlocking_extra_rules(
    monkeypatch,
    slots,
):
    """出題不能な行は飛ばすが、枠外のルールまで開放しない."""
    save = battle_state().save
    save.bossClears["book"] = [5]
    save.specialties = [
        Specialty(
            id="specialty_missing",
            name="出題元なし",
            resource_ids=[uuid4().hex],
            skill_id="specialty_burst",
            equipped=True,
        ),
    ]
    save.gambits = [
        GambitRule(action="specialty", definition_id="specialty_missing"),
        GambitRule(definition_id="heal"),
    ]
    monkeypatch.setattr(
        end_actions,
        "get_game_catalog",
        AsyncMock(return_value=default_catalog()),
    )
    monkeypatch.setattr(
        end_actions,
        "gambit_capacity",
        AsyncMock(return_value=SimpleNamespace(slots=slots)),
    )
    monkeypatch.setattr(
        end_actions,
        "specialty_capacity",
        AsyncMock(return_value={"slots": 1}),
    )
    monkeypatch.setattr(
        end_actions,
        "fetch_resource_growth",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(end_actions, "specialty_pools", AsyncMock(return_value=[]))
    selected, _, quizzes = await end_actions.select_end_candidate(uuid4(), save)
    assert (selected.skill.id if selected else None) == ("heal" if slots == 2 else None)
    assert quizzes == []
    assert len(save.gambits) == 2
    assert save.specialties[0].equipped


@mark_async_test()
async def test_practice_end_gambit_never_writes_live_state(u):
    """同じターン終了処理でも、模擬戦の回復・MP消費は本番に保存しない."""
    state = await intermission(u)
    original = await read_state(u.uid)
    state.save.run.hp = 5
    state.save.gambits = [GambitRule(definition_id="heal")]
    session = PracticeSession(
        state=state,
        context=await combat_context(u.uid),
        seed=1,
        server_now=0,
        catalog=default_catalog(),
    )
    result = practice_action(
        PracticeAction(
            session=session,
            body={"battle_id": state.save.battle.id, "turn": state.save.battle.turn},
        ),
    )
    assert result["state"].save.run.hp == 25
    assert result["state"].save.run.mp == 7
    assert await read_state(u.uid) == original


def test_power_is_live_and_independent_of_fusion_order():
    """合計Powerの対数補正だけを使い、元スキルは変更しない."""
    skill = RecoverySkill(
        id="heal",
        name="回復",
        effect="heal_hp",
        target="self",
        amount=10,
    )
    specialty = Specialty(
        id="specialty_test",
        name="回復",
        skill_id="heal",
        resource_ids=["a", "b"],
    )
    resources = {"a": SimpleNamespace(power=20), "b": SimpleNamespace(power=30)}
    before = strengthened(skill, specialty, resources, 2).amount
    specialty.resource_ids.reverse()
    assert strengthened(skill, specialty, resources, 2).amount == before
    resources["a"].power = 200
    assert strengthened(skill, specialty, resources, 2).amount > before
    assert skill.amount == 10


def test_bubble_scales_current_hp_on_apply_restore_and_expiry():
    """30/100→60/200、20被ダメージ後の解除で20/100になる."""
    state = battle_state()
    run, marker = state.save.run, state.save.battle
    run.hp, run.maxHp = 30, 100
    skill = BuffSkill(
        id="bubble",
        name="バブル",
        effect="buff",
        target="self",
        stat="maxHp",
        amount_mode="percent",
        amount=100,
        duration_turns=2,
    )
    add_effect(marker, skill, "player")
    marker.turn = 1
    sync_bubble(run, marker)
    assert (run.hp, run.maxHp, run.hpScale) == (60, 200, 2)
    restored = restore_state(state.model_dump_json(), GameBalance(base_hp=100))
    assert (restored.save.run.hp, restored.save.run.maxHp) == (60, 200)
    run.hp -= 20
    marker.turn = 3
    sync_bubble(run, marker)
    assert (run.hp, run.maxHp, run.hpScale) == (20, 100, 1)


def test_correct_ratio_applies_effect_once_and_status_is_binary():
    """二問中一問正解・誤答時半減なら75%の回復。状態付与は不発."""
    state = battle_state()
    marker = state.save.battle
    skill = RecoverySkill(
        id="heal",
        name="回復",
        effect="heal_hp",
        target="self",
        amount=20,
    )
    action = {
        "id": "action",
        "name": "回復",
        "skill": skill.model_dump(),
        "quiz_ids": ["a", "b"],
        "index": 2,
        "correct": 1,
        "wrong_multiplier": 0.5,
        "enemy_hp": {"a": 100, "b": 100},
    }
    marker.endAction = action
    finish_action(state)
    assert state.save.run.hp == 25
    assert marker.endAction is None
    assert state.save.endActionReceipt["action_id"] == "action"
    skill = AilmentSkill(id="poison", name="毒", effect="ailment", amount=5)
    marker.endAction = {**action, "skill": skill.model_dump()}
    finish_action(state)
    assert not marker.effects


@mark_async_test()
async def test_end_heal_uses_current_hp_not_normal_quiz_results_and_spends_once(u):
    """通常回答を参照せずターン末のHPから回復を選び、再送で再消費しない."""
    state = await intermission(u)
    state.save.run.hp = 5
    state.save.gambits = [
        GambitRule(condition="hp_below", threshold=30, definition_id="heal"),
    ]
    await seed_state(u, state)
    request = EndActionRequest(
        battle_id=state.save.battle.id,
        turn=state.save.battle.turn,
    )
    result = await start_end_action(u.uid, request)
    assert result["state"].save.run.hp == 25
    assert result["state"].save.run.mp == 7
    again = await start_end_action(u.uid, request)
    assert again["state"].revision == result["state"].revision
    assert again["state"].save.run.mp == 7


@mark_async_test()
async def test_fusion_consumes_parents_and_never_recreates_a_resource(u):
    """配合後も元リソースの所有権は保持し、重複作成と偽装保存を拒否する."""
    state = await intermission(u)
    state.save.battle = None
    state.save.battleFeedback = None
    state.save.run.phase = "path"
    first = state.save.run.resourceId.replace("-", "")
    second = uuid4().hex
    state.save.bossClears[second] = [5]
    await seed_state(u, state)
    for resource in (first, second):
        state = await update_specialties(
            u.uid,
            SpecialtyUpdate(
                revision=state.revision,
                operation="create",
                resource_id=resource,
                skill_id="specialty_burst",
            ),
        )
    parents = [s.id for s in state.save.specialties]
    state = await update_specialties(
        u.uid,
        SpecialtyUpdate(
            revision=state.revision,
            operation="fuse",
            ids=parents,
            skill_id="specialty_burst",
        ),
    )
    assert len(state.save.specialties) == 1
    assert set(state.save.specialties[0].resource_ids) == {first, second}
    assert not state.save.specialties[0].equipped
    with pytest.raises(HTTPException):
        await update_specialties(
            u.uid,
            SpecialtyUpdate(
                revision=state.revision,
                operation="create",
                resource_id=first,
                skill_id="specialty_burst",
            ),
        )
    forged = state.save.model_copy(deep=True)
    forged.specialties = []
    retained = await write_state(
        u.uid,
        StateUpdate(revision=state.revision, save=forged),
    )
    assert retained.save.specialties == state.save.specialties


@mark_async_test()
async def test_fused_quizzes_are_sequential_independent_and_idempotent(u, monkeypatch):
    """二つの出題元を一問ずつ、個別の締切で出題し消費は一回."""
    state = await intermission(u)
    source = state.save.run.resourceId.replace("-", "")
    other = uuid4().hex
    quiz_ids = [q["quiz_id"] for q in state.save.content["quizzes"]]
    monkeypatch.setattr(
        end_actions,
        "specialty_pools",
        AsyncMock(
            return_value=[
                {"resource_id": source, "quiz_ids": [quiz_ids[0]]},
                {"resource_id": other, "quiz_ids": [quiz_ids[1]]},
            ],
        ),
    )
    state.save.specialties = [
        Specialty(
            id="specialty_test",
            name="専門分野",
            skill_id="specialty_burst",
            resource_ids=[source, other],
            equipped=True,
        ),
    ]
    state.save.gambits = [
        GambitRule(action="specialty", definition_id="specialty_test"),
    ]
    await seed_state(u, state)
    result = await start_end_action(
        u.uid,
        EndActionRequest(
            battle_id=state.save.battle.id,
            turn=state.save.battle.turn,
            enemy_hp=dict.fromkeys(state.save.battle.enemies, 10000),
        ),
    )
    action = result["action"]
    assert len(action["quiz_ids"]) == 2
    request = EndActionAnswer(
        action_id=action["id"],
        index=0,
        selected=list(result["quiz"].correct),
    )
    first = await answer_end_action(u.uid, request)
    assert first["action"]["index"] == 1
    assert first["state"].save.run.mp == 7
    again = await answer_end_action(u.uid, request)
    assert again["action"]["index"] == 1
    assert again["state"].revision == first["state"].revision
    last = await answer_end_action(
        u.uid,
        EndActionAnswer(action_id=action["id"], index=1, selected=None),
    )
    assert last["action"] is None
    assert last["state"].save.run.mp == 7
    assert last["outgoing_damage"]
    retried = await answer_end_action(
        u.uid,
        EndActionAnswer(action_id=action["id"], index=1, selected=None),
    )
    assert retried["outgoing_damage"] == last["outgoing_damage"]
    assert (await read_state(u.uid)).revision == last["state"].revision
