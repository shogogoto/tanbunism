"""test."""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tanbun import config
from tanbun.feature.user.routers import auth_router, user_router

from .client import (
    AuthGet,
    AuthPatch,
    AuthPost,
    save_credential,
)


@pytest.fixture
def client() -> TestClient:
    """Test util."""
    api = FastAPI()
    api.include_router(auth_router())
    api.include_router(user_router())
    return TestClient(api)


# @pytest.fixture
# def cred(client: TestClient) -> None:


def test_crud_user(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """User CRUD flow."""
    monkeypatch.setattr(config, "_F", tmp_path / "credentials.json")
    prefix = f"user-{uuid4()}"
    email = f"{prefix}@example.com"
    password = "password"  # noqa: S105
    p = AuthPost(client=client.post)
    assert not p.login(email, password).is_success
    res = p.register(email, password)
    assert res.is_success
    login_res = p.login(email, password)
    assert login_res.is_success
    save_credential(login_res)

    g = AuthGet(client=client.get)
    res = g.me()
    assert res.is_success
    assert res.json()["email"] == email
    d = res.json().get("created")
    assert d is not None
    email2 = f"{prefix}@gmail.com"
    pa = AuthPatch(client=client.patch)
    res = pa.change_me(email2)

    assert res.is_success
    assert res.json()["email"] == email2


def test_update_duplicated_id():
    """既存のidに変更したときエラー."""


def test_update_duplicated_email():
    """既存のemailに変更したときエラー."""


# def test_update_additional(client: TestClient):
#     """登録後にユーザー属性を更新する."""
#     email = f"user-{uuid4()}@example.com"
#     password = "password"
#     p = AuthPost(client=client.post)
#     assert not p.login(email, password).is_success
#     res = p.register(email, password)
#     assert res.is_success
#     login_res = p.login(email, password)
#     assert login_res.is_success
#     save_credential(login_res)
#
#     g = AuthGet(client=client.get)
#     res = g.me()
#     assert res.is_success
#
#     assert res.json()["email"] == email
#     email2 = "user@gmail.com"
#     pa = AuthPatch(client=client.patch)
#     res = pa.change_me(email2)
#     assert res.is_success
#     assert res.json()["email"] == email2
