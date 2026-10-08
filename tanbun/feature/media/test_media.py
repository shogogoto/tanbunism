"""Never call the real Cloudinary account from tests."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from neomodel import adb
from starlette import status

from tanbun.config.env import Settings
from tanbun.conftest import mark_async_test
from tanbun.feature.admin.repo import delete_user_account
from tanbun.feature.user.db import AccountDB
from tanbun.feature.user.testing import aauth_header, aregister

from .cloudinary import CloudinaryClient, avatar_id, sign, upload_signature
from .repo import schedule_delete
from .router import eligible
from .worker import process_next


@pytest.fixture(autouse=True)
def cloud(monkeypatch):
    """Set dummy credentials; mock network separately."""
    for key, value in {
        "CLOUDINARY_CLOUD_NAME": "test-cloud",
        "CLOUDINARY_API_KEY": "dummy",
        "CLOUDINARY_API_SECRET": "dummy",
        "CLOUDINARY_UPLOAD_PRESET": "avatars",
    }.items():
        monkeypatch.setenv(key, value)


def _params(uid: UUID) -> dict[str, str]:
    return {
        "timestamp": str(int(time.time())),
        "public_id": f"{uid}/{uuid4()}",
        "folder": "avatar",
        "upload_preset": "avatars",
        "overwrite": "false",
        "source": "uw",
    }


def _url(public_id: str) -> str:
    return f"https://res.cloudinary.com/test-cloud/image/upload/c_crop,w_100,h_100/v123/{public_id}.jpg"


@pytest.mark.parametrize(
    "changed",
    [
        {"folder": "other"},
        {"public_id": str(uuid4())},
        {"upload_preset": "unsigned"},
        {"eager": "w_9000"},
        {"overwrite": "true"},
        {"timestamp": "1"},
        {"timestamp": "invalid"},
        {"custom_coordinates": "1,2,0,0"},
    ],
)
def test_signing_rejects_untrusted_fields(changed):
    """Reject stale timestamps, other folders, presets and arbitrary transforms."""
    uid = uuid4()
    with pytest.raises(HTTPException) as error:
        upload_signature(_params(uid) | changed, uid, Settings())
    assert error.value.status_code == status.HTTP_400_BAD_REQUEST


def test_signing_and_legacy_identity():
    """Respect Cloudinary serialization and crop/version URL identities."""
    uid = uuid4()
    params = _params(uid)
    assert upload_signature(params, uid, Settings()) == sign(params, "dummy")
    with pytest.raises(HTTPException):
        upload_signature(params, uuid4(), Settings())
    public_id = f"avatar/{uid}/{uuid4()}"
    assert avatar_id(_url(public_id), Settings()) == public_id
    assert avatar_id(_url(f"avatar/{uid.hex}"), Settings()) == f"avatar/{uid.hex}"
    assert (
        avatar_id(_url(public_id).replace("test-cloud", "another"), Settings()) is None
    )
    assert avatar_id("https://example.com/avatar.jpg", Settings()) is None
    old = datetime.now(UTC) - timedelta(days=4)
    asset = {
        "public_id": public_id,
        "created_at": old.isoformat(),
        "version": int(old.timestamp()),
    }
    assert eligible(asset, set(), Settings())
    assert not eligible(asset, {public_id}, Settings())
    assert not eligible(asset | {"version": int(time.time())}, set(), Settings())


@mark_async_test()
async def test_authenticated_signing_and_deletion(ac: AsyncClient):
    """Legacy arbitrary public IDs are not accepted; DB reference clears atomically."""
    user = await aregister("avatar@example.com")
    headers = await aauth_header(user.email)
    params = _params(UUID(user.uid))
    body = {"params": params}
    assert (
        await ac.post("/user/avatar/sign", json=body)
    ).status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        await ac.post("/user/avatar/sign", json=body, headers=headers)
    ).status_code == status.HTTP_200_OK
    public_id = f"avatar/{params['public_id']}"
    user.avatar_url = _url(public_id)
    await user.save()
    assert (
        await ac.get("/admin/images", headers=headers)
    ).status_code == status.HTTP_403_FORBIDDEN
    assert (
        await ac.delete("/user/avatar", headers=headers)
    ).status_code == status.HTTP_200_OK
    await user.refresh()
    assert not user.avatar_url
    rows, _ = await adb.cypher_query("MATCH (j:ImageCleanup) RETURN j.public_id")
    assert rows == [[public_id]]


@mark_async_test()
async def test_replacement_and_both_account_deletion_paths():
    """Preserve the queue after user deletion, including the admin deletion path."""
    user = await aregister("replace-avatar@example.com")
    original = f"avatar/{UUID(user.uid)}"
    replacement = f"avatar/{UUID(user.uid)}/{uuid4()}"
    user.avatar_url = _url(original)
    await user.save()
    db = AccountDB()
    updated = await db.update(
        await db.get(UUID(user.uid)),
        {"avatar_url": _url(replacement)},
    )
    await db.delete(updated)
    other = await aregister("delete-avatar@example.com")
    other_id = f"avatar/{UUID(other.uid)}"
    other.avatar_url = _url(other_id)
    await other.save()
    assert await delete_user_account(other.uid)
    rows, _ = await adb.cypher_query("MATCH (j:ImageCleanup) RETURN j.public_id")
    assert {row[0] for row in rows} == {original, replacement, other_id}


@mark_async_test()
async def test_worker_protects_references_and_retries(monkeypatch):
    """Used avatars survive; provider failures leave durable jobs for retry."""
    user = await aregister("live-avatar@example.com")
    public_id = f"avatar/{UUID(user.uid)}/{uuid4()}"
    user.avatar_url = _url(public_id)
    await user.save()
    destroy = AsyncMock()
    monkeypatch.setattr(CloudinaryClient, "destroy", destroy)
    await schedule_delete(public_id, delay=0)
    assert await process_next(adb)
    destroy.assert_not_awaited()
    user.avatar_url = ""
    await user.save()
    await schedule_delete(public_id, delay=0)
    destroy.side_effect = HTTPException(502, "offline")
    assert await process_next(adb)
    rows, _ = await adb.cypher_query(
        "MATCH (j:ImageCleanup) RETURN j.attempts, j.lease",
    )
    assert rows == [[1, None]]
    destroy.side_effect = None
    await adb.cypher_query("MATCH (j:ImageCleanup) SET j.due=datetime()")
    assert await process_next(adb)
    rows, _ = await adb.cypher_query("MATCH (j:ImageCleanup) RETURN count(j)")
    assert rows == [[0]]


@mark_async_test()
async def test_admin_audit_cleanup_rechecks_and_profile_ownership(
    ac: AsyncClient,
    monkeypatch,
):
    """Stale selections, newly overwritten assets and foreign avatars are protected."""
    admin = await aregister("image-admin@example.com")
    admin.is_superuser = True
    await admin.save()
    headers = await aauth_header(admin.email)
    other = await aregister("image-other@example.com")
    other_id = f"avatar/{UUID(other.uid)}"
    other.avatar_url = _url(other_id)
    await other.save()
    response = await ac.patch(
        "/user/me",
        headers=headers,
        json={"avatar_url": other.avatar_url},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    orphan = f"avatar/{uuid4()}"
    recent = f"avatar/{uuid4()}"
    old = datetime.now(UTC) - timedelta(days=4)

    def asset(public_id):
        return {
            "public_id": public_id,
            "created_at": old.isoformat(),
            "version": int(time.time())
            if public_id == recent
            else int(old.timestamp()),
            "bytes": 1234,
        }

    request = AsyncMock(
        return_value={
            "resources": [asset(orphan), asset(other_id), asset(recent)],
            "next_cursor": "next",
        },
    )
    monkeypatch.setattr(CloudinaryClient, "request", request)
    response = await ac.get("/admin/images", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert [item["can_delete"] for item in response.json()["resources"]] == [
        True,
        False,
        True,
    ]
    assert response.json()["next_cursor"] == "next"
    request.side_effect = [asset(orphan), asset(recent)]
    response = await ac.post(
        "/admin/images/cleanup",
        headers=headers,
        json={
            "public_ids": [orphan, other_id, recent, "avatar/unknown", "other/folder"],
        },
    )
    assert response.json()["scheduled"] == [orphan]
    rows, _ = await adb.cypher_query("MATCH (j:ImageCleanup) RETURN j.public_id")
    assert rows == [[orphan]]


@mark_async_test()
async def test_cleanup_scheduling_rolls_back_with_database_changes():
    """External cleanup must not survive a failed account/avatar transaction."""
    public_id = f"avatar/{uuid4()}"
    await adb.begin()
    await schedule_delete(public_id)
    await adb.rollback()
    rows, _ = await adb.cypher_query("MATCH (j:ImageCleanup) RETURN count(j)")
    assert rows == [[0]]


@mark_async_test()
async def test_sign_rate_limit_and_retry(ac: AsyncClient):
    """Count distinct uploads, allow retries, and reject ownership alias bypasses."""
    user = await aregister("avatar-limit@example.com")
    headers = await aauth_header(user.email)
    params = _params(UUID(user.uid))
    body = {"params": params}
    response = await ac.post("/user/avatar/sign", json=body, headers=headers)
    assert response.status_code == status.HTTP_200_OK
    for _ in range(19):
        await schedule_delete(f"avatar/{UUID(user.uid)}/{uuid4()}")
    response = await ac.post(
        "/user/avatar/sign",
        json={"params": _params(UUID(user.uid))},
        headers=headers,
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert (
        await ac.post("/user/avatar/sign", json=body, headers=headers)
    ).status_code == status.HTTP_200_OK
    params["public_id"] = params["public_id"].replace(str(UUID(user.uid)), user.uid)
    assert (
        await ac.post("/user/avatar/sign", json=body, headers=headers)
    ).status_code == status.HTTP_400_BAD_REQUEST


async def test_destroy_invalidates_and_acknowledges(monkeypatch):
    """Treat already-deleted images as success, never send arbitrary folder IDs."""
    client = CloudinaryClient(Settings())
    request = AsyncMock(return_value={"result": "not found"})
    monkeypatch.setattr(client, "request", request)
    public_id = f"avatar/{uuid4()}"
    await client.destroy(public_id)
    request.assert_awaited_once_with(
        "POST",
        "image/destroy",
        data={"public_id": public_id, "invalidate": "true"},
    )
    with pytest.raises(ValueError, match="Unmanaged"):
        await client.destroy("another/asset")


@mark_async_test()
async def test_only_one_worker_deletes_at_a_time(monkeypatch):
    """Another process must wait even if a different job is eligible."""
    for _ in range(2):
        await schedule_delete(f"avatar/{uuid4()}", delay=0)
    started, release = asyncio.Event(), asyncio.Event()

    async def destroy(_client, _public_id):
        started.set()
        await release.wait()

    monkeypatch.setattr(CloudinaryClient, "destroy", destroy)
    task = asyncio.create_task(process_next(adb))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert not await process_next(adb)
    finally:
        release.set()
        await task
    assert await process_next(adb)
