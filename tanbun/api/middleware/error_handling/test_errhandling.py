"""test."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j.exceptions import Neo4jError

from tanbun.feature.domain.errors import DomainError

from . import ErrorHandlingMiddleware

app = FastAPI()
app.add_middleware(ErrorHandlingMiddleware)


MSG = "user defined message"
CD = 400


class UserDefError(DomainError):  # noqa: D101
    status_code = CD
    msg = MSG


@app.get("/test")
def for_test() -> None:  # noqa: D103
    raise UserDefError


@app.get("/test2")
def for_test2() -> None:  # noqa: D103
    msg = "from arg"
    raise UserDefError(msg)


client = TestClient(app)


@app.get("/database-timeout")
def database_timeout() -> None:
    """サーバー期限超過を再現する."""
    raise Neo4jError._hydrate_neo4j(  # noqa: SLF001
        code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
        message="Transaction timed out",
    )


def test_database_timeout_returns_504() -> None:
    """実行エラーもcommitエラーと同じ期限超過応答になる."""
    response = client.get("/database-timeout")
    assert response.status_code == 504  # noqa: PLR2004
    assert response.json()["detail"]["code"] == 504  # noqa: PLR2004
    assert "時間上限" in response.json()["detail"]["message"]


def test_api() -> None:  # noqa: D103
    res = client.get(url="/test")
    assert res.status_code == CD
    assert res.json()["detail"]["code"] == CD
    assert res.json()["detail"]["message"] == MSG


def test_api2() -> None:  # noqa: D103
    res = client.get(url="/test2")
    assert res.status_code == CD
    assert res.json()["detail"]["code"] == CD
    assert res.json()["detail"]["message"] == "from arg"
