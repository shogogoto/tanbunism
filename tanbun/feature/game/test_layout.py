"""領域整列は道だけを補修し、進行・報酬・戦闘を変えない."""
# ruff: noqa: PLR2004

from uuid import uuid4

import pytest
from httpx import AsyncClient
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .admin import aligned_region_map
from .state import DungeonMap, GameSave, GameState, Run, read_state


def branched_map() -> DungeonMap:
    """現在地と領域を維持する旧形式の枝分かれ."""
    return DungeonMap.model_validate({
        "current": "b",
        "places": [
            {"id": "a", "region": 0},
            {"id": "b", "region": 0},
            {"id": "c", "region": 1},
            {"id": "d", "region": 1},
        ],
        "edges": [
            {"from": "@entrance", "to": "a", "kind": "detour"},
            {"from": "a", "to": "b", "kind": "relation"},
            {"from": "b", "to": "c", "kind": "relation"},
            {"from": "a", "to": "d", "kind": "relation"},
        ],
    })


def test_alignment_keeps_regions_location_and_only_existing_relation_edges():
    """領域間の関係を偽造せず、各領域を入口から一列にする."""
    original = branched_map()
    aligned = aligned_region_map(original)
    assert aligned.current == original.current
    assert aligned.places == original.places
    assert [(e.from_, e.to, e.kind) for e in aligned.edges] == [
        ("@entrance", "a", "detour"),
        ("a", "b", "relation"),
        ("@entrance", "c", "detour"),
        ("c", "d", "detour"),
    ]
    assert original.edges[2].from_ == "b"
    assert aligned_region_map(aligned) == aligned


@mark_async_test()
@pytest.mark.parametrize("parked", [False, True])
async def test_admin_alignment_persists_once_and_preserves_active_or_parked_run(
    ac: AsyncClient,
    *,
    parked: bool,
):
    """管理者だけが修正でき、再操作は無変更。戦闘結果がある場合も拒否する."""
    user = await aregister("layout@example.com")
    headers = await aauth_header(user.email)
    resource = uuid4()
    key = str(resource)
    run = Run(
        resourceId=key,
        name="本",
        hp=20,
        maxHp=35,
        maxMp=10,
        attack=10,
        defense=1,
        moves=2,
        kills=1,
        enemyHp=0,
        enemyMaxHp=1,
        quizCursor=2,
        readIds=["a", "b", "c", "d"],
        phase="path",
    )
    save = GameSave(
        run=None if parked else run,
        maps={key: branched_map()},
        dungeons={key: {"run": run}} if parked else {},
        clears={key: 3},
        content={"quizzes": [], "regionQuizPools": {"0": ["q"]}},
    )
    state = GameState(revision=4, save=save)
    url = f"/admin/users/{user.uid}/game/dungeons/{resource}/regions/align"
    assert (await ac.post(url, headers=headers)).status_code == 403
    user.is_superuser = True
    await user.save()
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state, u.game_revision=4",
        {"uid": user.uid, "state": state.model_dump_json(by_alias=True)},
    )
    response = await ac.post(url, headers=headers)
    assert response.is_success, response.text
    assert response.json() == {"changed": True, "place_count": 4, "region_count": 2}
    latest = await read_state(user.uid)
    assert latest.revision == 5
    assert latest.save.run == save.run
    assert latest.save.dungeons == save.dungeons
    assert latest.save.content == save.content
    assert latest.save.clears == save.clears
    assert latest.save.maps[key] == aligned_region_map(save.maps[key])
    assert not (await ac.post(url, headers=headers)).json()["changed"]
    assert (await read_state(user.uid)).revision == latest.revision
    latest.save.battleFeedback = "結果処理中"
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state",
        {"uid": user.uid, "state": latest.model_dump_json(by_alias=True)},
    )
    assert (await ac.post(url, headers=headers)).status_code == 409
