"""対象付き開放キーのimport配布・消費・権限・再送の検査."""
# ruff: noqa: PLR2004

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from neo4j.exceptions import TransientError
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.notification.repo import list_notifications
from tanbun.feature.user.testing import aauth_header, aregister

from . import dungeon_keys
from .dungeon_keys import grant_dungeon_key, key_products
from .economy import EconomyAction, change_economy
from .state import GameSave, StateUpdate, read_state, write_state
from .test_economy import seed_user
from .unlocks import DungeonUnlock


async def resource_for(user_id: str) -> str:
    """空ではない所有リソース一つ."""
    resource = uuid4().hex
    await adb.cypher_query(
        """MATCH (u:User {uid:$user})
        CREATE (r:Resource {uid:$resource, title:'学びの本'})-[:OWNED]->(u)
        CREATE (:Sentence {uid:$resource, resource_uid:$resource})""",
        {"user": user_id, "resource": resource},
    )
    return resource


@mark_async_test()
async def test_grants_once_uses_once_and_preserves_current_adventure():
    """再importでも増えず、使用は開放だけでHPや歩数・現在地を変えない."""
    user = await seed_user()
    resource = await resource_for(user.uid)
    granted = await grant_dungeon_key(user.uid, resource)
    identity = f"@dungeon:{resource}"
    assert granted.save.economy.inventory[identity] == 1
    assert not granted.save.unlockedDungeons
    again = await grant_dungeon_key(user.uid, resource)
    assert again.revision == granted.revision
    product = key_products(again.save)[0]
    assert product.resource_id == resource
    assert product.price is None
    assert product.enabled
    body = EconomyAction(revision=again.revision, product_id=identity)
    used = await change_economy(user.uid, body, "use")
    # Inventory operations refresh the configured quiz-time base, not an active timer.
    assert used.save.run.model_dump(
        exclude={"answerSeconds"},
    ) == again.save.run.model_dump(exclude={"answerSeconds"})
    assert used.save.economy.gold == again.save.economy.gold
    assert used.save.economy.inventory[identity] == 0
    assert used.save.unlockedDungeons == [
        DungeonUnlock(
            sourceId=resource,
            resourceId=resource,
            name="学びの本",
            achievement=0,
            source="item",
        ),
    ]
    assert key_products(used.save) == []
    with pytest.raises(HTTPException) as stale:
        await change_economy(user.uid, body, "use")
    assert stale.value.status_code == 409
    imported_again = await grant_dungeon_key(user.uid, resource)
    assert imported_again.save.economy.inventory[identity] == 0
    assert imported_again.revision == used.revision
    feed = await list_notifications(user.uid, limit=100)
    assert len(feed.notifications) == 1
    notification = feed.notifications[0]
    assert notification.kind == "dungeon_key_granted"
    assert notification.title == "ダンジョン開放キーを入手しました"
    assert "学びの本" in notification.description
    assert notification.href == "/game/item"


@mark_async_test()
async def test_parallel_grants_do_not_duplicate_inventory():
    """同時importを繰り返しても、リソースごとにキー・通知が一個だけになる."""
    user = await seed_user()
    for _ in range(10):
        resource = await resource_for(user.uid)
        await asyncio.gather(
            grant_dungeon_key(user.uid, resource),
            grant_dungeon_key(user.uid, resource),
        )
        assert (await read_state(user.uid)).save.economy.inventory[
            f"@dungeon:{resource}"
        ] == 1
    assert len((await list_notifications(user.uid, limit=100)).notifications) == 10


@mark_async_test()
async def test_deleted_or_foreign_resources_cannot_be_opened_or_consumed():
    """所持履歴があっても現時点で利用不能なら消費しない."""
    user = await seed_user()
    resource = await resource_for(user.uid)
    granted = await grant_dungeon_key(user.uid, resource)
    identity = f"@dungeon:{resource}"
    await adb.cypher_query(
        "MATCH (r:Resource {uid:$resource})-[o:OWNED]->() DELETE o",
        {"resource": resource},
    )
    with pytest.raises(HTTPException) as denied:
        await change_economy(
            user.uid,
            EconomyAction(revision=granted.revision, product_id=identity),
            "use",
        )
    assert denied.value.status_code == 404
    assert (await read_state(user.uid)) == granted


@mark_async_test()
async def test_already_opened_key_is_disabled_and_is_not_consumed():
    """抽選などで先に開放された場合は所持したまま二重使用を防ぐ."""
    user = await seed_user()
    resource = await resource_for(user.uid)
    state = await grant_dungeon_key(user.uid, resource)
    state.save.unlockedDungeons.append(
        DungeonUnlock(
            sourceId=resource,
            resourceId=resource,
            name="学びの本",
            achievement=3,
        ),
    )
    await adb.cypher_query(
        "MATCH (u:User {uid:$user}) SET u.game_state=$state",
        {"user": user.uid, "state": state.model_dump_json()},
    )
    assert not key_products(state.save)[0].enabled
    with pytest.raises(HTTPException) as opened:
        await change_economy(
            user.uid,
            EconomyAction(revision=state.revision, product_id=f"@dungeon:{resource}"),
            "use",
        )
    assert opened.value.status_code == 409
    assert (await read_state(user.uid)).save.economy.inventory[
        f"@dungeon:{resource}"
    ] == 1


@mark_async_test()
async def test_old_or_forged_snapshot_cannot_mint_or_remove_keys():
    """所持品はサーバー管理。旧クライアントの保存でも配布履歴を失わない."""
    user = await seed_user()
    resource = await resource_for(user.uid)
    previous = await grant_dungeon_key(user.uid, resource)
    forged = previous.save.model_copy(deep=True)
    forged.economy.dungeon_keys = {}
    forged.economy.inventory[f"@dungeon:{uuid4().hex}"] = 100
    updated = await write_state(
        user.uid,
        StateUpdate(revision=previous.revision, save=forged),
    )
    assert updated.save.economy == previous.save.economy


@mark_async_test()
async def test_text_and_file_import_distribute_targeted_keys_once(ac):
    """実際のテキストimportとファイルimportの両方で配布する."""
    user = await aregister("dungeon-keys@example.com")
    headers = await aauth_header(user.email)
    text = "# 読書メモ\n  学んだ内容\n"
    response = await ac.post(
        "/resource-text",
        headers=headers,
        json={"txt": text, "path": ["memo.txt"]},
    )
    assert response.is_success, response.text
    resource = response.json()["resource_id"]
    key_id = f"@dungeon:{resource}"
    assert (await read_state(user.uid)).save.economy.inventory[key_id] == 1
    repeated = await ac.post(
        "/resource-text",
        headers=headers,
        json={"txt": text, "path": ["memo.txt"]},
    )
    assert repeated.is_success, repeated.text
    uploaded = await ac.post(
        "/resource",
        headers=headers,
        files=[
            (
                "files",
                ("another.txt", "# 別の本\n  新しい学び\n".encode(), "text/plain"),
            ),
        ],
    )
    assert uploaded.is_success, uploaded.text
    state = await read_state(user.uid)
    assert len(state.save.economy.dungeon_keys) == 2
    assert state.save.economy.inventory[key_id] == 1
    shop = await ac.get("/game/shop", headers=headers)
    assert shop.is_success, shop.text
    assert {
        p["resource_id"]
        for p in shop.json()["products"]
        if p["kind"] == "dungeon_unlock"
    } == {resource, *uploaded.json()["resource_ids"]}
    assert state.save == GameSave.model_validate(state.save.model_dump())
    feed = await list_notifications(user.uid, limit=100)
    assert len(feed.notifications) == 2
    assert all(n.kind == "dungeon_key_granted" for n in feed.notifications)


@mark_async_test()
async def test_import_does_not_notify_when_dungeon_is_already_open():
    """未配布でも既に開放済みなら個数ゼロなので通知しない."""
    user = await seed_user()
    resource = await resource_for(user.uid)
    state = await read_state(user.uid)
    state.save.visitedDungeons.append(resource)
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state",
        {"uid": user.uid, "state": state.model_dump_json()},
    )
    granted = await grant_dungeon_key(user.uid, resource)
    assert granted.save.economy.inventory[f"@dungeon:{resource}"] == 0
    assert not (await list_notifications(user.uid, limit=100)).notifications


@mark_async_test()
async def test_notification_failure_rolls_back_key_distribution(monkeypatch):
    """通知保存の失敗で配布だけが確定し、以後通知できなくならない."""
    user = await seed_user()
    resource = await resource_for(user.uid)
    previous = await read_state(user.uid)
    monkeypatch.setattr(
        "tanbun.feature.game.dungeon_keys.create_notification",
        AsyncMock(side_effect=RuntimeError("notification failed")),
    )
    with pytest.raises(RuntimeError, match="notification failed"):
        await grant_dungeon_key(user.uid, resource)
    assert await read_state(user.uid) == previous
    assert not (await list_notifications(user.uid, limit=100)).notifications


@mark_async_test()
async def test_notification_deadlock_rolls_back_then_commits_once(monkeypatch):
    """通知作成後の競合でもDB全体を破棄し、キーと通知を一回だけ確定する."""
    user = await seed_user()
    resource = await resource_for(user.uid)
    previous = await read_state(user.uid)
    original = dungeon_keys.create_notification
    attempts = 0

    async def create_then_deadlock(*args, **kwargs):
        nonlocal attempts
        notification = await original(*args, **kwargs)
        attempts += 1
        if attempts == 1:
            raise TransientError._hydrate_neo4j(  # noqa: SLF001
                code="Neo.TransientError.Transaction.DeadlockDetected",
                message="deadlock after notification creation",
            )
        return notification

    dispatch = AsyncMock()
    monkeypatch.setattr(dungeon_keys, "create_notification", create_then_deadlock)
    monkeypatch.setattr(dungeon_keys, "dispatch_saved_notifications", dispatch)
    granted = await grant_dungeon_key(user.uid, resource)
    assert attempts == 2
    assert granted.revision == previous.revision + 1
    assert granted.save.economy.inventory[f"@dungeon:{resource}"] == 1
    assert await read_state(user.uid) == granted
    assert len((await list_notifications(user.uid, limit=100)).notifications) == 1
    dispatch.assert_awaited_once()
