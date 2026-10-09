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
