"""Exercise provider failures without a database or real Cloudinary requests."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette import status

from tanbun.config.env import Settings
from tanbun.feature.user.routers import images as router

from .cloudinary import CloudinaryClient, avatar_id
from .repo import referenced_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (401, "認証"),
        (403, "権限"),
        (404, "Cloud Name"),
        (420, "利用上限"),
        (429, "利用上限"),
        (500, "要求が失敗"),
    ],
)
async def test_provider_status_is_safe_and_actionable(monkeypatch, code, message):
    """Do not echo provider bodies, credentials, or request URLs to the browser."""
    response = httpx.Response(
        code,
        json={"error": {"message": "secret-provider-detail"}},
        request=httpx.Request("GET", "https://example.com/private-cloud"),
    )
    monkeypatch.setattr(httpx.AsyncClient, "request", AsyncMock(return_value=response))
    with pytest.raises(HTTPException) as error:
        await _client().request("GET", "resources/image/upload")
    assert error.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert str(code) in error.value.detail
    assert message in error.value.detail
    assert "secret" not in error.value.detail
    assert "private-cloud" not in error.value.detail


def _client():
    return CloudinaryClient(
        Settings(
            CLOUDINARY_CLOUD_NAME="private-cloud",
            CLOUDINARY_API_KEY="private-key",
            CLOUDINARY_API_SECRET="private-secret",  # noqa: S106
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status_code", "message"),
    [
        (httpx.ReadTimeout("secret"), 504, "タイムアウト"),
        (httpx.ConnectError("secret"), 502, "通信に失敗"),
    ],
)
async def test_connection_failures(monkeypatch, failure, status_code, message):
    """Separate timeouts from connection failures without disclosing internals."""
    monkeypatch.setattr(httpx.AsyncClient, "request", AsyncMock(side_effect=failure))
    with pytest.raises(HTTPException) as error:
        await _client().request("GET", "resources/image/upload")
    assert error.value.status_code == status_code
    assert message in error.value.detail
    assert "secret" not in error.value.detail


@pytest.mark.asyncio
async def test_invalid_response(monkeypatch):
    """Malformed JSON is not reported as an authentication failure."""
    response = httpx.Response(
        200,
        text="secret",
        request=httpx.Request("GET", "https://example.com"),
    )
    monkeypatch.setattr(httpx.AsyncClient, "request", AsyncMock(return_value=response))
    with pytest.raises(HTTPException) as error:
        await _client().request("GET", "resources/image/upload")
    assert "応答を読み取れません" in error.value.detail
    assert "secret" not in error.value.detail


@pytest.mark.asyncio
async def test_inventory_includes_preview_and_reference_state(monkeypatch):
    """Return provider delivery URLs without changing deletion protection."""
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "test")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "dummy")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "dummy")
    url = "https://res.cloudinary.com/test/image/upload/v123/avatar/live.jpg"
    monkeypatch.setattr(
        CloudinaryClient,
        "request",
        AsyncMock(
            return_value={
                "resources": [
                    {"public_id": "avatar/live", "secure_url": url},
                    {"public_id": "avatar/recent"},
                ],
            },
        ),
    )
    monkeypatch.setattr(
        router,
        "referenced_ids",
        AsyncMock(return_value={"avatar/live"}),
    )
    monkeypatch.setattr(
        router.adb,
        "cypher_query",
        AsyncMock(return_value=([[0, 0]], [])),
    )
    result = await router.list_images(None)
    live, recent = result["resources"]
    assert live["url"] == url
    assert live["referenced"]
    assert not live["can_delete"]
    assert recent["url"] is None
    assert not recent["referenced"]
    assert recent["can_delete"]


@pytest.mark.asyncio
async def test_manual_deletion_rechecks_references_and_scope(monkeypatch):
    """Allow non-UUID/recent avatars, including a newly referenced selection."""
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "test")
    refs = AsyncMock(side_effect=[{"avatar/live"}, {"avatar/new-live"}, set()])
    monkeypatch.setattr(router, "referenced_ids", refs)
    destroy = AsyncMock()
    monkeypatch.setattr(CloudinaryClient, "destroy", destroy)
    result = await router.delete_images(
        router.CleanupRequest(
            public_ids=[
                "other/test",
                "avatar/live",
                "avatar/new-live",
                "avatar/test",
                "avatar/test",
            ],
        ),
        None,
    )
    assert result["deleted"] == ["avatar/test"]
    assert result["skipped"] == ["other/test", "avatar/live", "avatar/new-live"]
    destroy.assert_awaited_once_with("avatar/test", manual=True)


@pytest.mark.asyncio
async def test_manual_destroy_only_relaxes_uuid_rule(monkeypatch):
    """Keep automatic cleanup strict and manual calls inside the avatar folder."""
    client = _client()
    request = AsyncMock(return_value={"result": "ok"})
    monkeypatch.setattr(client, "request", request)
    with pytest.raises(ValueError, match="Unmanaged"):
        await client.destroy("avatar/test")
    with pytest.raises(ValueError, match="Unmanaged"):
        await client.destroy("other/test", manual=True)
    await client.destroy("avatar/test", manual=True)
    assert request.await_count == 1
    url = "https://res.cloudinary.com/private-cloud/image/upload/v123/avatar/test.jpg"
    assert avatar_id(url, client.settings, managed_only=False) == "avatar/test"
    assert avatar_id(url, client.settings) is None


@pytest.mark.asyncio
async def test_reference_inventory_includes_non_uuid_avatars(monkeypatch):
    """Manual deletion must protect legacy/test IDs in actual user properties."""
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "private-cloud")
    database = AsyncMock()
    database.cypher_query.return_value = (
        [
            [
                "https://res.cloudinary.com/private-cloud/image/upload/v123/avatar/test.jpg",
            ],
            ["https://res.cloudinary.com/another-cloud/image/upload/avatar/test.jpg"],
            ["https://google.example/avatar.jpg"],
        ],
        [],
    )
    assert await referenced_ids(database) == {"avatar/test"}
