"""MP・ボス開放・ランダムな専門分野クイズの実戦連動."""
# ruff: noqa: PLR2004

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aauth_header

from . import boss, combat
from .balance import Allocation, GameBalance
from .boss import BossRequest
from .population import replace_region_pool
from .skills import (
    SkillAnswer,
    SkillRequest,
    resolve_skill,
    specialty_pools,
    start_skill,
)
from .state import GameState, MapPlace, StateUpdate, write_state
from .test_combat import answers, encounter

u = async_fixture()(fx_u)


async def seed_state(user: LUser, state: GameState) -> None:
    """テストだけで必要な開拓履歴を置く。通常APIで開放は偽造できない."""
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state, u.game_revision=$revision",
        {
            "uid": user.uid,
            "state": state.model_dump_json(by_alias=True),
            "revision": state.revision,
        },
    )


def test_mp_is_allocated_without_automatic_level_growth():
    """最大MPを初期値と割り振りだけで算出する."""
    assert (
        Allocation(mp=3).stats(GameBalance(base_mp=12, mp_per_point=5))["maxMp"] == 27
    )


@mark_async_test()
async def test_boss_unlocks_specialty_and_does_not_count_as_a_regular_lap(
    u: LUser,
    monkeypatch,
):
    """達成度・歩数を検査し、ボス勝利の節目だけを保存する."""
    state = await encounter(u)
    assert state.save.run.mp == state.save.run.maxMp == 10
    state.save.battle = None
    state.save.run.phase = "path"
    resource = state.save.run.resourceId
    state.save.maps[resource].places = [
        MapPlace(id=uuid4().hex, region=i // 5) for i in range(25)
    ]
    state.save.maps[resource].current = "@entrance"
    state.save.maps[resource].edges = []
    # Pydantic models are required for re-reading this fixture's map.
    state = GameState.model_validate(state.model_dump(by_alias=True))
    await seed_state(u, state)
    quizzes = [q["quiz_id"].replace("-", "") for q in state.save.content["quizzes"]]
    monkeypatch.setattr(
        boss,
        "get_or_prepare_region_pool",
        AsyncMock(return_value={"ready": True, "quiz_ids": quizzes}),
    )
    monkeypatch.setattr(boss, "_validate_quiz_ids", AsyncMock(return_value=True))
    with pytest.raises(HTTPException):
        await boss.start_boss(u.uid, BossRequest(revision=state.revision, level=10))
    started = await boss.start_boss(
        u.uid,
        BossRequest(revision=state.revision, level=5),
    )
    assert started.save.run.moves == 2
    assert started.save.battle.bossLevel == 5
    assert len((await combat.combat_context(u.uid)).enemies[-1].quizIndexes) == 2
    marker = started.save.battle
    result = await combat.settle_turn(
        u.uid,
        combat.TurnRequest(
            battle_id=marker.id,
            turn=0,
            answers=answers(started),
            defeated=marker.enemies,
        ),
    )
    saved = GameState.model_validate(result["state"])
    assert saved.save.bossClears[resource.replace("-", "")] == [5]
    assert saved.save.clears == {}
    forged = saved.save.model_copy(deep=True)
    forged.bossClears = {resource.replace("-", ""): [20]}
    retained = await write_state(
        u.uid,
        StateUpdate(revision=saved.revision, save=forged),
    )
    assert retained.save.bossClears == saved.save.bossClears


async def intermission(user: LUser) -> GameState:
    """実際の回答を保存した後、攻略済み母集団を持つターン間の状態."""
    started = await encounter(user)
    marker = started.save.battle
    result = await combat.settle_turn(
        user.uid,
        combat.TurnRequest(battle_id=marker.id, turn=0, answers=answers(started)),
    )
    state = GameState.model_validate(result["state"])
    resource = state.save.run.resourceId.replace("-", "")
    state.save.bossClears = {resource: [5]}
    await replace_region_pool(
        user.uid,
        resource,
        5,
        [q["quiz_id"].replace("-", "") for q in state.save.content["quizzes"]],
    )
    await seed_state(user, state)
    return state


@mark_async_test()
async def test_specialty_spends_mp_once_and_records_one_real_answer(u: LUser):
    """リソース→クイズの抽選、再開、全体攻撃、XPの冪等な記録."""
    state = await intermission(u)
    assert len(await specialty_pools(u.uid, state)) == 1
    request = SkillRequest(battle_id=state.save.battle.id, turn=state.save.battle.turn)
    session = await start_skill(u.uid, request)
    assert session["state"].save.run.mp == 7
    repeated = await start_skill(u.uid, request)
    assert repeated["skill"].id == session["skill"].id
    assert repeated["state"].revision == session["state"].revision
    body = SkillAnswer(
        skill_id=session["skill"].id,
        selected=list(session["quiz"].correct),
        enemy_hp=dict.fromkeys(state.save.battle.enemies, 1),
    )
    before = (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][0]
    result = await resolve_skill(u.uid, body)
    assert result["correct"]
    assert len(result["outgoing_damage"]) == 2
    assert result["state"].save.run.mp == 7
    assert result["state"].save.economy.gold == 60
    assert (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][
        0
    ] == before + 1
    retry = await resolve_skill(u.uid, body)
    assert retry["state"].revision == result["state"].revision
    assert (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][
        0
    ] == before + 1


@mark_async_test()
@pytest.mark.parametrize("abandon", [False, True])
async def test_unanswered_skill_damages_without_answer_xp_or_double_mp(
    u: LUser,
    *,
    abandon: bool,
):
    """時間切れとブラウザ中断も消費済みMP・罰ダメージを維持する."""
    state = await intermission(u)
    marker = state.save.battle
    session = await start_skill(
        u.uid,
        SkillRequest(battle_id=marker.id, turn=marker.turn),
    )
    before = (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][0]
    if abandon:
        updated = await combat.abandon_combat(u.uid)
        assert updated.save.battle is None
    else:
        updated = (
            await resolve_skill(u.uid, SkillAnswer(skill_id=session["skill"].id))
        )["state"]
        with pytest.raises(HTTPException):
            await start_skill(
                u.uid,
                SkillRequest(
                    battle_id=updated.save.battle.id,
                    turn=updated.save.battle.turn,
                ),
            )
    assert updated.save.run.mp == 7
    assert updated.save.run.hp < state.save.run.hp
    assert (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][
        0
    ] == before


@mark_async_test()
async def test_simultaneous_death_never_unlocks_a_specialty(u: LUser):
    """ボス撃破とHP0が同時でも敗北を優先する."""
    state = await encounter(u)
    state.save.battle.bossLevel = 5
    state.save.run.hp = 1
    marker = state.save.battle
    result = combat.apply_turn_result(
        state,
        combat.TurnRequest(
            battle_id=marker.id,
            turn=marker.turn,
            defeated=marker.enemies,
        ),
        dict.fromkeys(marker.enemies, True),
        1,
        Allocation().stats(GameBalance()),
    )
    assert result.save.run.phase == "defeated"
    assert result.save.bossClears == {}


@mark_async_test()
async def test_specialty_routes_return_real_quiz_and_updated_mp(
    u: LUser,
    ac: AsyncClient,
):
    """HTTPでも実クイズのモデルを返し、本人の回答だけを保存する."""
    state = await intermission(u)
    marker = state.save.battle
    headers = await aauth_header(u.email)
    response = await ac.post(
        "/game/battle/skill/start",
        headers=headers,
        json={"battle_id": marker.id, "turn": marker.turn},
    )
    assert response.is_success, response.text
    session = response.json()
    assert session["state"]["save"]["run"]["mp"] == 7
    response = await ac.post(
        "/game/battle/skill/answer",
        headers=headers,
        json={
            "skill_id": session["skill"]["id"],
            "selected": session["quiz"]["correct"],
            "enemy_hp": dict.fromkeys(marker.enemies, 1),
        },
    )
    assert response.is_success, response.text
    assert response.json()["correct"]
    assert response.json()["state"]["save"]["run"]["mp"] == 7
