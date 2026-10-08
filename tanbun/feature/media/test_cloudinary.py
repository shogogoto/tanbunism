"""Exercise provider failures without a database or real Cloudinary requests."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette import status

from tanbun.config.env import Settings

from .cloudinary import CloudinaryClient


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
