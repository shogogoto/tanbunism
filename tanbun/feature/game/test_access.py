"""時計区切り・権限・同時消費・学習実績の独立性."""

import asyncio
from uuid import uuid4

from httpx import AsyncClient
from neomodel import adb
from pytest_mock import MockerFixture

from tanbun.conftest import mark_async_test
from tanbun.feature.gamification.usecase import fetch_learning_progress
from tanbun.feature.user.testing import aauth_header, aregister

from .access import access_status


def test_fixed_clock_slots() -> None:
    """10:25に消費しても10:30で回復、未使用分は蓄積しない."""
    at_10 = 10 * 3600
    consumed = at_10 // 1800
    before = access_status(at_10 + 25 * 60, consumed)
    assert not before.available
    assert before.next_available_at == (at_10 + 30 * 60) * 1000
    assert not access_status(at_10 + 30 * 60 - 0.001, consumed).available
    assert access_status(at_10 + 30 * 60, consumed).available
    assert access_status(at_10 + 3 * 3600, consumed).available
    assert not access_status(at_10, consumed + 1).available


@mark_async_test()
async def test_permission_recovery_and_admin_reset(
    ac: AsyncClient,
    mocker: MockerFixture,
) -> None:
    """本人のみ消費、adminのみ解除。他人の状態・XP・HPには触れない."""
    clock = mocker.patch(
        "tanbun.feature.game.access.time",
        return_value=10 * 3600 + 25 * 60,
    )
    target = await aregister("adventure@example.com")
    other = await aregister("other-adventure@example.com")
    admin = await aregister("adventure-admin@example.com")
    admin.is_superuser = True
    await admin.save()
    headers = await aauth_header(target.email)
    other_headers = await aauth_header(other.email)
    admin_headers = await aauth_header(admin.email)
    path = "/game/adventure-access"
    reset_path = f"/admin/users/{target.uid}/adventure-reset"
    assert (await ac.get(path)).status_code == 401  # noqa: PLR2004
    assert (await ac.post(f"{path}/consume")).status_code == 401  # noqa: PLR2004
    initial = await ac.get(path, headers=headers)
    assert initial.json()["available"]
    assert initial.headers["cache-control"] == "no-store"
    before_xp = await fetch_learning_progress(target.uid)
    await adb.cypher_query(
        "MATCH (u:User {uid: $uid}) SET u.test_game_hp = 24",
        params={"uid": target.uid},
    )
    consumed = await ac.post(f"{path}/consume", headers=headers)
    assert consumed.status_code == 200  # noqa: PLR2004
    assert consumed.json()["next_available_at"] == (10 * 3600 + 30 * 60) * 1000
    assert not consumed.json()["available"]
    assert (
        await ac.patch("/user/me", headers=headers, json={"profile": "changed"})
    ).is_success
    assert not (await ac.get(path, headers=headers)).json()["available"]
    assert (await ac.post(f"{path}/consume", headers=headers)).status_code == 409  # noqa: PLR2004
    assert (await ac.get(path, headers=other_headers)).json()["available"]
    assert (await ac.post(reset_path)).status_code == 401  # noqa: PLR2004
    assert (await ac.post(reset_path, headers=other_headers)).status_code == 403  # noqa: PLR2004
    assert not (await ac.get(path, headers=headers)).json()["available"]
    for _ in range(2):
        assert (await ac.post(reset_path, headers=admin_headers)).json()["available"]
    assert (await ac.post(f"{path}/consume", headers=headers)).is_success
    assert (await ac.post(f"{path}/consume", headers=headers)).status_code == 409  # noqa: PLR2004
    clock.return_value = 10 * 3600 + 30 * 60
    assert (await ac.get(path, headers=headers)).json()["available"]
    assert (await ac.post(f"{path}/consume", headers=headers)).is_success
    assert not (await ac.get(path, headers=headers)).json()["available"]
    assert (await fetch_learning_progress(target.uid)) == before_xp
    rows, _ = await adb.cypher_query(
        "MATCH (u:User {uid: $uid}) RETURN u.test_game_hp",
        params={"uid": target.uid},
    )
    assert rows[0][0] == 24  # noqa: PLR2004
    assert (
        await ac.post(f"/admin/users/{uuid4()}/adventure-reset", headers=admin_headers)
    ).status_code == 404  # noqa: PLR2004


@mark_async_test()
async def test_only_one_concurrent_consumer(
    ac: AsyncClient,
    mocker: MockerFixture,
) -> None:
    """同じユーザーの別端末からの競合は一方だけを許可する."""
    mocker.patch("tanbun.feature.game.access.time", return_value=10 * 3600)
    target = await aregister("concurrent-adventure@example.com")
    headers = await aauth_header(target.email)
    responses = await asyncio.gather(
        *(ac.post("/game/adventure-access/consume", headers=headers) for _ in range(2)),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
