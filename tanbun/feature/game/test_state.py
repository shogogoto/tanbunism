"""冒険スナップショットの端末間共有と競合."""

import asyncio

from httpx import AsyncClient

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .state import GameSave, Run


@mark_async_test()
async def test_shared_state_and_conflicts(ac: AsyncClient) -> None:
    """本人の保存だけを共有し、古い端末からの上書きは拒否する."""
    user = await aregister("game-state@example.com")
    other = await aregister("other-game-state@example.com")
    headers = await aauth_header(user.email)
    other_headers = await aauth_header(other.email)
    path = "/game/state"
    assert (await ac.get(path)).status_code == 401  # noqa: PLR2004
    initial = await ac.get(path, headers=headers)
    assert initial.headers["cache-control"] == "no-store"
    payload = {"revision": 0, "save": {"version": 2, "clears": {"book": 2}}}
    responses = await asyncio.gather(
        *(ac.put(path, headers=headers, json=payload) for _ in range(2)),
    )
    assert sorted(item.status_code for item in responses) == [200, 409]
    shared = (await ac.get(path, headers=headers)).json()
    assert shared["revision"] == 1
    assert shared["save"]["clears"] == {"book": 2}
    assert shared["save"]["visitedDungeons"] == ["book"]
    assert (await ac.get(path, headers=other_headers)).json()["revision"] == 0
    assert (
        await ac.patch("/user/me", headers=headers, json={"profile": "new"})
    ).is_success
    assert (await ac.get(path, headers=headers)).json() == shared
    payload["revision"] = 1
    payload["consume_access"] = True
    assert (await ac.put(path, headers=headers, json=payload)).is_success
    payload["revision"] = 2
    assert (await ac.put(path, headers=headers, json=payload)).status_code == 409  # noqa: PLR2004
    assert (await ac.get(path, headers=headers)).json()["revision"] == 2  # noqa: PLR2004


@mark_async_test()
async def test_route_order_survives_reopening_and_event_rest(ac: AsyncClient) -> None:
    """選択順と単文内容はDBに保存され、休憩後も現在地が変わらない."""
    user = await aregister("game-route@example.com")
    headers = await aauth_header(user.email)
    route = ["c", "a", "e", "b", "d"]
    save = GameSave(
        run=Run(
            resourceId="book",
            name="本",
            hp=24,
            maxHp=35,
            attack=10,
            defense=1,
            moves=5,
            kills=1,
            enemyHp=0,
            enemyMaxHp=20,
            quizCursor=2,
            readIds=route,
            phase="rest",
        ),
        content={
            "knowledge": [{"uid": uid, "sentence": f"知識 {uid}"} for uid in route],
        },
    )
    path = "/game/state"
    assert (
        await ac.put(
            path,
            headers=headers,
            json={"revision": 0, "save": save.model_dump()},
        )
    ).is_success
    restored = (await ac.get(path, headers=headers)).json()
    assert restored["save"]["run"]["readIds"] == route
    assert restored["save"]["content"] == save.content
    assert restored["save"]["visitedDungeons"] == ["book"]
    resumed = restored["save"]
    resumed["run"]["moves"] = 0
    resumed["run"]["phase"] = "path"
    assert (
        await ac.put(path, headers=headers, json={"revision": 1, "save": resumed})
    ).is_success
    reopened = (await ac.get(path, headers=headers)).json()
    assert reopened["save"]["run"]["readIds"] == route
    assert reopened["save"]["run"]["readIds"][-1] == "d"
    stale = await ac.put(
        path,
        headers=headers,
        json={"revision": 0, "save": GameSave().model_dump()},
    )
    assert stale.status_code == 409  # noqa: PLR2004
    assert (await ac.get(path, headers=headers)).json() == reopened


@mark_async_test()
async def test_visited_dungeons_survive_retreat_and_old_clients(
    ac: AsyncClient,
) -> None:
    """未攻略でも履歴を共有し、再訪は先頭へ。クライアントから削除させない."""
    user = await aregister("game-history@example.com")
    other = await aregister("other-history@example.com")
    headers = await aauth_header(user.email)
    other_headers = await aauth_header(other.email)
    path = "/game/state"
    run = {
        "resourceId": "first",
        "name": "最初の本",
        "hp": 35,
        "maxHp": 35,
        "attack": 10,
        "defense": 1,
        "moves": 0,
        "kills": 0,
        "enemyHp": 0,
        "enemyMaxHp": 20,
        "quizCursor": 0,
        "readIds": [],
        "phase": "path",
    }
    revision = 0

    async def save(payload: dict) -> dict:
        nonlocal revision
        response = await ac.put(
            path,
            headers=headers,
            json={"revision": revision, "save": payload},
        )
        assert response.is_success
        result = response.json()
        revision = result["revision"]
        return result["save"]

    entered = await save({"run": run})
    assert entered["visitedDungeons"] == ["first"]
    # Retreat without the new field, as an old deployed client would do.
    retreated = await save({"clears": {}})
    assert retreated["visitedDungeons"] == ["first"]
    assert retreated["run"] is None
    reopened = (await ac.get(path, headers=headers)).json()
    assert reopened["save"] == retreated
    assert (await ac.get(path, headers=other_headers)).json()["save"][
        "visitedDungeons"
    ] == []
    second = await save({"run": {**run, "resourceId": "second"}})
    assert second["visitedDungeons"] == ["second", "first"]
    unchanged = await save({"run": second["run"], "visitedDungeons": ["fake"]})
    assert unchanged["visitedDungeons"] == ["second", "first"]
    await save({"run": None})
    revisited = await save({"run": run})
    assert revisited["visitedDungeons"] == ["first", "second"]
    assert revisited["clears"] == {}


@mark_async_test()
async def test_maps_and_parked_dungeons_survive_server_reads(ac: AsyncClient) -> None:
    """分岐・達成度帯・ダンジョン別HPをDBに保存し、旧クライアントから守る."""
    user = await aregister("game-map@example.com")
    headers = await aauth_header(user.email)
    run = Run(
        resourceId="book",
        name="本",
        hp=24,
        maxHp=35,
        attack=10,
        defense=1,
        moves=3,
        kills=1,
        enemyHp=0,
        enemyMaxHp=20,
        quizCursor=2,
        readIds=["a", "b", "a"],
        phase="path",
    )
    map_data = {
        "current": "a",
        "places": [{"id": "a", "region": 0}, {"id": "b", "region": 1}],
        "edges": [
            {"from": "@entrance", "to": "a", "kind": "detour"},
            {"from": "a", "to": "b", "kind": "relation"},
        ],
    }
    payload = {
        "maps": {"book": map_data},
        "dungeons": {"book": {"run": run.model_dump(), "content": {"quizzes": []}}},
    }
    path = "/game/state"
    response = await ac.put(
        path,
        headers=headers,
        json={"revision": 0, "save": payload},
    )
    assert response.is_success, response.text
    restored = (await ac.get(path, headers=headers)).json()
    assert restored["save"]["maps"]["book"] == map_data
    assert restored["save"]["dungeons"]["book"]["run"]["hp"] == run.hp
    # Deployed older clients cannot erase fields they do not know about.
    response = await ac.put(path, headers=headers, json={"revision": 1, "save": {}})
    assert response.is_success, response.text
    reopened = (await ac.get(path, headers=headers)).json()
    assert reopened["save"]["maps"] == restored["save"]["maps"]
    assert reopened["save"]["dungeons"] == restored["save"]["dungeons"]
    response = await ac.put(
        path,
        headers=headers,
        json={"revision": 2, "save": {**payload, "run": run.model_dump()}},
    )
    assert response.is_success, response.text
    assert response.json()["save"]["dungeons"] == {}
    invalid = {**map_data, "current": "missing"}
    response = await ac.put(
        path,
        headers=headers,
        json={"revision": 3, "save": {"maps": {"book": invalid}}},
    )
    assert response.status_code == 422  # noqa: PLR2004
