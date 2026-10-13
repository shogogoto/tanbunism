"""ログの認証情報取得と、CORS付きエラー応答の再発防止."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from fastapi_users.jwt import generate_jwt
from starlette.requests import Request

from tanbun.api import CorsFastAPI
from tanbun.api.middleware.logging import LoggingMiddleware, req2user_id
from tanbun.config.env import Settings
from tanbun.feature.user.backend import get_strategy


def request(token):
    """Bearerトークンだけを持つHTTPリクエスト."""
    return Request({
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })


def test_signed_logging_subject_never_reads_user_database(mocker):
    """ログは署名・期限・audienceを検証するが、ユーザー照会をしない."""
    read = mocker.patch(
        "tanbun.feature.user.db.AccountDB.get",
        side_effect=AssertionError("DB access"),
    )
    uid = uuid4()
    strategy = get_strategy()
    token = generate_jwt(
        {"sub": str(uid), "aud": strategy.token_audience},
        strategy.secret,
        lifetime_seconds=60,
        algorithm=strategy.algorithm,
    )
    assert req2user_id(request(token)) == uid.hex
    read.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    ["invalid", "expired", "wrong-audience", "wrong-signature"],
)
def test_invalid_logging_token_is_not_trusted(kind):
    """未検証のJWTのsubをログのユーザーIDに採用しない."""
    strategy = get_strategy()
    token = "invalid"  # noqa: S105
    if kind != "invalid":
        token = generate_jwt(
            {
                "sub": str(uuid4()),
                "aud": ["other"]
                if kind == "wrong-audience"
                else strategy.token_audience,
            },
            "wrong" if kind == "wrong-signature" else strategy.secret,
            lifetime_seconds=-1 if kind == "expired" else 60,
            algorithm=strategy.algorithm,
        )
    assert req2user_id(request(token)) is None


def test_logging_lookup_failure_does_not_break_request(mocker):
    """ログ情報取得失敗でも、エンドポイントの認証・処理は通常通り行う."""
    app = CorsFastAPI()
    app.add_middleware(LoggingMiddleware)
    mocker.patch(
        "tanbun.api.middleware.logging.req2user_id",
        side_effect=RuntimeError("logging failure"),
    )

    @app.get("/test")
    def endpoint():
        return {"ok": True}

    response = TestClient(app).get("/test")
    assert response.json() == {"ok": True}
    assert response.headers["x-request-id"]


def test_unhandled_error_response_still_has_cors():
    """ServerErrorMiddlewareが作った500もブラウザが読める."""
    app = CorsFastAPI()

    @app.get("/test")
    def endpoint():
        msg = "unexpected error"
        raise RuntimeError(msg)

    origin = Settings().allow_origins[0]
    response = TestClient(app, raise_server_exceptions=False).get(
        "/test",
        headers={"Origin": origin},
    )
    assert response.status_code == 500  # noqa: PLR2004
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
