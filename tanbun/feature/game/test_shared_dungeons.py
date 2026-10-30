"""他ユーザーの公開リソースを、自分だけの進行で遊ぶ."""
# ruff: noqa: PLR2004

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.entry.resource.repo.owner import check_entry_owner
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.quiz.learning.fixture import fx_learning, learning_resource_id
from tanbun.feature.quiz.learning.study_plan.repo import (
    ensure_default_resource_study_plan,
)
from tanbun.feature.user.testing import aauth_header, aregister

from . import router
from .combat import EncounterRequest, TurnRequest, settle_turn, start_combat
from .knowledge import KnowledgeValidation, valid_knowledge
from .preparation import _prepare_resource_to_frontier
from .shared_dungeons import dungeon_accessible, join_dungeon
from .state import DungeonMap, Run, StateUpdate, read_state, write_state


@mark_async_test()
async def test_join_and_prepare_independent_dungeon(ac: AsyncClient, monkeypatch):
    """参加・探索・クイズ・XPを所有者のデータから分離する."""
    owner = await fx_learning()
    resource = await learning_resource_id(owner.uid)
    learner = await aregister("dungeon-guest@example.com")
    resource = to_uuid(resource)
    headers = await aauth_header(learner.email)
    monkeypatch.setattr(router, "schedule_dungeon_quizzes", AsyncMock())
    path = f"/game/dungeons/{resource}/join"
    assert (await ac.get("/game/dungeons/public")).status_code == 401
    assert (await ac.post(path, json={"revision": 0})).status_code == 401
    before_owner = await read_state(owner.uid)
    owner_plan = await ensure_default_resource_study_plan(owner.uid, resource, "title")
    rows, _ = await adb.cypher_query(
        "MATCH (s:Sentence {resource_uid:$uid}) RETURN s.uid LIMIT 1",
        {"uid": resource.hex},
    )
    validation = KnowledgeValidation(resource_id=resource, sentence_ids=[rows[0][0]])
    assert not await valid_knowledge(learner.uid, validation)
    assert not await dungeon_accessible(learner.uid, resource)
    listing = await ac.get("/game/dungeons/public?q=title", headers=headers)
    assert listing.status_code == 200
    assert listing.json()[0]["owner_id"] == owner.uid
    assert listing.json()[0]["joined"] is False
    response = await ac.post(path, json={"revision": 0}, headers=headers)
    assert response.is_success, response.text
    assert response.json()["save"]["unlockedDungeons"][0]["source"] == "shared"
    assert await dungeon_accessible(learner.uid, resource)
    assert not await check_entry_owner(learner.uid, resource)
    assert await valid_knowledge(learner.uid, validation) == rows[0]
    assert (
        await ac.post(path, json={"revision": 0}, headers=headers)
    ).status_code == 409
    assert (
        len(
            (await ac.post(path, json={"revision": 1}, headers=headers)).json()["save"][
                "unlockedDungeons"
            ],
        )
        == 1
    )
    guest_plan = await ensure_default_resource_study_plan(
        learner.uid,
        resource,
        "title",
    )
    assert guest_plan.uid != owner_plan.uid
    await _prepare_resource_to_frontier(to_uuid(learner.uid), resource.hex)
    pool = await ac.get(
        f"/game/dungeons/{resource}/regions/1/quiz-pool?include_quizzes=true",
        headers=headers,
    )
    assert pool.is_success, pool.text
    assert pool.json()["ready"] is True
    assert len(pool.json()["quizzes"]) == 5
    assert (await read_state(owner.uid)) == before_owner
    assert (await fetch_resource_growth(learner.uid))[0].total_xp == 0
    assert await fetch_resource_growth(learner.uid, owned_only=True) == []
    exposure = await ac.post(
        f"/dashboard/tanbuns/{rows[0][0]}/exposures",
        headers=headers,
    )
    assert exposure.is_success, exposure.text
    assert (await fetch_resource_growth(learner.uid))[0].total_xp > 0
    assert (await fetch_resource_growth(owner.uid))[0].total_xp == 0
    await play_guest_turn(learner.uid, resource.hex, rows[0][0], pool.json()["quizzes"])
    assert await read_state(owner.uid) == before_owner
    assert (await fetch_resource_growth(owner.uid))[0].total_xp == 0


async def play_guest_turn(user_id, resource_id, sentence_id, quizzes):
    """実際の戦闘回答APIも参加者用クイズだけで精算できる."""
    state = await read_state(user_id)
    state.save.run = Run(
        resourceId=resource_id,
        name="title",
        hp=10,
        maxHp=10,
        attack=3,
        defense=3,
        moves=1,
        kills=0,
        enemyHp=0,
        enemyMaxHp=1,
        quizCursor=0,
        readIds=[sentence_id],
        phase="battle",
    )
    state.save.maps[resource_id] = DungeonMap(
        current=sentence_id,
        places=[{"id": sentence_id, "region": 0}],
        edges=[],
    )
    state.save.content = {
        "quizzes": quizzes,
        "regionEnemies": {"0": [{"id": "guest-enemy", "name": "敵", "quizIndex": 0}]},
    }
    state = await write_state(
        user_id,
        StateUpdate(revision=state.revision, save=state.save),
    )
    state = await start_combat(
        user_id,
        EncounterRequest(revision=state.revision, region=0, scheduled=True),
    )
    target = state.save.battle.schedule.quiz_enemy_id
    quiz = state.save.content["quizzes"][state.save.battle.quizIndices[target]]
    receipt = await settle_turn(
        user_id,
        TurnRequest(
            battle_id=state.save.battle.id,
            turn=state.save.battle.turn,
            answers={target: quiz["correct"]},
        ),
    )
    assert receipt["results"] == {target: True}


@mark_async_test()
async def test_missing_dungeon_does_not_mutate_guest():
    """存在しないIDや自分のリソースを参加APIで開放できない."""
    user = await fx_learning()
    before = await read_state(user.uid)
    for resource in (uuid4(), to_uuid(await learning_resource_id(user.uid))):
        with pytest.raises(HTTPException) as error:
            await join_dungeon(to_uuid(user.uid), resource, 0)
        assert error.value.status_code == 404
    assert await read_state(user.uid) == before
