"""達成度による恒久開放と、端末間の保存."""

import operator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from neomodel import adb

from tanbun.conftest import mark_async_test

from . import state as module
from . import unlocks
from .state import DungeonMap, GameSave, GameState, StateUpdate, write_state


def explored_map(count: int) -> DungeonMap:
    """到達済みのマップ."""
    return DungeonMap(
        places=[{"id": str(uuid4()), "region": index // 5} for index in range(count)],
    )


@pytest.mark.parametrize(("count", "expected"), [(14, 0), (15, 1), (29, 1), (30, 2)])
@mark_async_test()
async def test_every_three_achievement_levels_unlock_one_distinct_dungeon(
    monkeypatch,
    count,
    expected,
):
    """15地点ごとの開放は保存され、読み直しても再抽選しない."""
    source, other, another = uuid4().hex, uuid4().hex, uuid4().hex
    monkeypatch.setattr(
        unlocks.adb,
        "cypher_query",
        AsyncMock(
            return_value=([[source, "元の本"], [other, "本A"], [another, "本B"]], None),
        ),
    )
    monkeypatch.setattr(unlocks, "choice", operator.itemgetter(0))
    save = GameSave(maps={source: explored_map(count)})
    await unlocks.discover_dungeons(uuid4(), save)
    assert len(save.unlockedDungeons) == expected
    assert len({item.resourceId for item in save.unlockedDungeons}) == expected
    assert [item.achievement for item in save.unlockedDungeons] == [
        step * 3 for step in range(1, expected + 1)
    ]
    restored = GameSave.model_validate_json(save.model_dump_json(by_alias=True))
    assert not await unlocks.discover_dungeons(uuid4(), restored)
    assert restored.unlockedDungeons == save.unlockedDungeons


@mark_async_test()
async def test_snapshot_cannot_add_or_remove_server_unlocks(monkeypatch):
    """クライアントの偽の開放や古いスナップショットを採用しない."""
    record = unlocks.DungeonUnlock(
        sourceId="source",
        resourceId="target",
        name="本",
        achievement=3,
    )
    previous = GameState(save=GameSave(unlockedDungeons=[record]))
    monkeypatch.setattr(module, "read_state", AsyncMock(return_value=previous))
    persist = AsyncMock(return_value=[[True]])
    monkeypatch.setattr(module, "compare_and_save", persist)
    forged = unlocks.DungeonUnlock(
        sourceId="forged",
        resourceId="fake",
        name="偽",
        achievement=3,
    )
    saved = await write_state(
        uuid4(),
        StateUpdate(revision=0, save=GameSave(unlockedDungeons=[forged])),
    )
    assert saved.save.unlockedDungeons == [record]


@mark_async_test()
async def test_unowned_source_does_not_unlock_dungeons(monkeypatch):
    """所有していない元リソースの開拓履歴では開放しない."""
    source = uuid4().hex
    monkeypatch.setattr(
        unlocks.adb,
        "cypher_query",
        AsyncMock(return_value=([[uuid4().hex, "別の本"]], None)),
    )
    save = GameSave(maps={source: explored_map(15)})
    assert not await unlocks.discover_dungeons(uuid4(), save)


@mark_async_test()
async def test_unlock_is_persisted_and_excludes_another_users_resource():
    """実DBでも本人のダンジョンだけが開き、次の読み込みで同じ道が残る."""
    user, source, target, foreign = [uuid4().hex for _ in range(4)]
    state = GameState(save=GameSave(maps={source: explored_map(15)}))
    await adb.cypher_query(
        """CREATE (user:User {uid:$user, game_state:$state})
        CREATE (other:User {uid:'other-user'})
        CREATE (source:Resource {uid:$source, title:'元の本'})-[:OWNED]->(user)
        CREATE (target:Resource {uid:$target, title:'次の本'})-[:OWNED]->(user)
        CREATE (foreign:Resource {uid:$foreign, title:'他人の本'})-[:OWNED]->(other)
        WITH source, target, foreign
        UNWIND [source, target, foreign] AS resource
        CREATE (:Sentence {uid:resource.uid, resource_uid:resource.uid})""",
        {
            "user": user,
            "source": source,
            "target": target,
            "foreign": foreign,
            "state": state.model_dump_json(by_alias=True),
        },
    )
    updated = await module.refresh_dungeon_unlocks(user, state)
    assert [item.resourceId for item in updated.save.unlockedDungeons] == [target]
    restored = await module.read_state(user)
    assert restored.save.unlockedDungeons == updated.save.unlockedDungeons
    again = await module.refresh_dungeon_unlocks(user, restored)
    assert again.revision == restored.revision
    assert again.save.unlockedDungeons == updated.save.unlockedDungeons
