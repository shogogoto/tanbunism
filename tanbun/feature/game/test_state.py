"""冒険スナップショットの端末間共有と競合."""

import asyncio

from httpx import AsyncClient

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister


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
