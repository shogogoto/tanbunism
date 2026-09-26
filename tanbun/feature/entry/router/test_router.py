"""router test."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import status
from fastapi.testclient import TestClient
from httpx import AsyncClient

from tanbun.api import api
from tanbun.conftest import mark_async_test
from tanbun.feature.entry.domain import ResourceInfo
from tanbun.feature.entry.namespace import (
    create_folder,
    create_resource,
    fetch_namespace,
)
from tanbun.feature.entry.namespace.sync import Anchor
from tanbun.feature.entry.namespace.test_namespace import files  # noqa: F401
from tanbun.feature.entry.resource.limits import DEFAULT_RESOURCE_UPLOAD_LIMITS
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.entry.router.fixture import fixture_txt
from tanbun.feature.user.label import LUser
from tanbun.feature.user.routers.repo.client import (
    AuthPost,
)
from tanbun.feature.user.testing import async_auth_header

if TYPE_CHECKING:
    from pathlib import Path


def auth_header(email: str = "user@example.com") -> tuple[TestClient, dict[str, str]]:
    """For fixture."""
    client = TestClient(api)
    p = AuthPost(client=client.post)
    password = "password"  # noqa: S105
    res = p.register(email, password)
    res = p.login(email, password)
    token = res.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    return client, h


@mark_async_test()
async def test_sync_router(files: tuple[Anchor, list[Path]]) -> None:  # noqa: F811, RUF029
    """Sync コマンドでの想定."""
    client, h = auth_header()

    anchor, paths = files
    meta = anchor.to_metas(paths)
    res = client.post(
        "/namespace",
        headers=h,
        json=meta.model_dump(mode="json"),
    )

    anchor, _ = files
    reqfiles = []
    op = []
    for p_ in res.json():
        p = anchor / p_
        f = p.open("rb")
        op.append(f)
        reqfiles.append(("files", (p.name, f, "application/octet-stream")))
    res = client.post(
        "/resource",
        headers=h,
        files=reqfiles,
    )
    [f.close() for f in op]
    assert res.is_success


@mark_async_test()
async def test_fetch_resource_detail(files: tuple[Anchor, list[Path]]):  # noqa: F811
    """fetch_resource_detailのテスト."""
    client, h = auth_header()
    user = await LUser.nodes.first()
    s = """
    # title
    ## head1
        aaa
        bbb
            ccc

    """

    res = client.post(
        "/resource-text",
        headers=h,
        json={"txt": s, "path": ["a", "b", "c"]},
    )
    assert res.is_success

    uid = res.json()["resource_id"]
    res = client.get(f"/resource/{uid}")
    assert res.is_success
    info = ResourceInfo.model_validate(res.json()["resource_info"])
    assert info.user.id.hex == user.uid
    assert info.resource.uid.hex == uid
    assert [folder.name for folder in info.folders] == ["a", "b"]

    res = client.get(f"/entry/{info.folders[-1].uid}")
    assert res.is_success
    detail = res.json()
    assert [entry["name"] for entry in detail["ancestors"]] == ["a"]
    assert detail["entry"]["name"] == "b"
    assert [entry["uid"].replace("-", "") for entry in detail["children"]] == [uid]
    assert "authors" in detail["children"][0]
    assert uid.replace("-", "") in detail["stats"]

    res = client.get(f"/user/{user.uid}/namespace")
    assert res.is_success
    assert res.json()["user_id"].replace("-", "") == user.uid


@mark_async_test()
async def test_reject_too_many_resources_in_one_request(ac: AsyncClient) -> None:
    """同期アップロードの件数上限をAPI入口で適用する."""
    headers = await async_auth_header()
    request_files = [
        ("files", (f"{index}.txt", b"# title", "text/plain"))
        for index in range(DEFAULT_RESOURCE_UPLOAD_LIMITS.max_files + 1)
    ]

    response = await ac.post("/resource", headers=headers, files=request_files)

    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


@mark_async_test()
async def test_restore_regression() -> None:
    """whenで 端がないとinfを返そうとしてエラーになる."""
    client, h = auth_header()
    u = await LUser.nodes.first()

    txt = """
    # title
      aaa
      ラ・フレージュ学院でラテン語、数学、古典、科学、スコラ哲学を学ぶ
        when. ~ 1612
    """
    _sn, r = await save_text(u.uid, txt)
    res = client.get(f"/resource/{r.uid}", headers=h)
    assert res.is_success


@mark_async_test()
async def test_restore_regression2() -> None:
    """frontendでjsonを解釈できるようにする."""
    client, h = auth_header()
    u = await LUser.nodes.first()
    _sn, r = await save_text(u.uid, fixture_txt())
    res = client.get(f"/resource/{r.uid}", headers=h)
    assert res.is_success


@mark_async_test()
async def test_delete_entry_router() -> None:
    """所有者がエントリを削除できる."""
    client, h1 = auth_header()
    _, h2 = auth_header("two@gmail.com")
    u = await LUser.nodes.get(email="user@example.com")
    _sn, r = await save_text(u.uid, "# title\n")

    # check owner
    res = client.delete(f"/entry/{r.uid}", headers=h2)
    assert res.status_code == status.HTTP_403_FORBIDDEN  # たまにCIでコケる
    res = client.delete(f"/entry/{r.uid}", headers=h1)
    assert res.is_success

    # delete entry
    f = await create_folder(u.uid, "f1")
    r = await create_resource(u.uid, "f1", "r21")

    res = client.delete(f"/entry/{f.uid}", headers=h1)
    assert res.status_code == status.HTTP_409_CONFLICT
    res = client.delete(f"/entry/{r.uid}", headers=h1)
    assert res.is_success
    assert client.delete(f"/entry/{f.uid}", headers=h1).is_success
    res = client.delete(f"/entry/{f.uid}", headers=h1)
    assert res.status_code == status.HTTP_404_NOT_FOUND


def reqfile(s: str, fname: str, p: Path):  # noqa: D103
    f = p / fname
    f.write_text(s)
    return [("files", (fname, f.read_bytes(), "application/octet-stream"))]


@mark_async_test()
async def test_post_resource_locking_new(ac: AsyncClient, tmp_path: Path):
    """リソース作成が同時に起きないようにロック."""
    h = await async_auth_header()

    s = """
        # title1
            aaa
    """
    task1 = ac.post("/resource", headers=h, files=reqfile(s, "a.txt", tmp_path))
    task2 = ac.post("/resource", headers=h, files=reqfile(s, "b.txt", tmp_path))

    results = await asyncio.gather(
        task1,
        task2,
    )

    assert status.HTTP_409_CONFLICT in [r.status_code for r in results]
    user = await LUser.nodes.get(email="one@gmail.com")
    ns = await fetch_namespace(user.uid)
    assert [resource.name for resource in ns.resources] == ["# title1"]


@mark_async_test()
async def test_post_resource_locking_upd(ac: AsyncClient, tmp_path: Path):
    """リソース更新が同時に起きないようにロック."""
    h = await async_auth_header()

    s = """
        # title1
            aaa
    """

    u = await LUser(email="one@gmail.com").nodes.first()
    await save_text(u.uid, s)

    upd = """
        # title1
            aaa
            bbb
            ccc
    """

    task1 = ac.post("/resource", headers=h, files=reqfile(upd, "a.txt", tmp_path))
    task2 = ac.post("/resource", headers=h, files=reqfile(upd, "b.txt", tmp_path))

    results = await asyncio.gather(
        task1,
        task2,
    )
    assert status.HTTP_409_CONFLICT in [r.status_code for r in results]


@mark_async_test()
async def test_post_resource_with_parse_error_message(
    ac: AsyncClient,
    tmp_path: Path,
):
    """テキストファイルのフォーマット不備を指摘するエラーメッセージを返す."""
    h = await async_auth_header()
    s1 = """
        # title1
            aaa
                    bbb
                ccc
    """
    res = await ac.post(
        "/resource",
        headers=h,
        files=reqfile(s1, "a.txt", tmp_path),
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST

    s2 = """
        # title1
            aaa
            aaa
            ccc
    """
    res = await ac.post(
        "/resource",
        headers=h,
        files=reqfile(s2, "a.txt", tmp_path),
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST


# @pytest.mark.skip
# @mark_async_test()
# async def test_post_for_debug(aclient: AsyncClient):
#     """debug用."""
#     h = await async_auth_header(aclient)
#
#     a = Anchor("~/Documents/notes").expanduser()
#     ps = [p for ext in ["md", "kn"] for p in a.rglob(f"*零*.{ext}")]
#     for p in ps:
#         print(p)
#         res = await aclient.post(
#             "/resource",
#             headers=h,
#             files=reqfile(p.read_text(), p.name, p.parent),
#         )
#         print(res.status_code, res.text[:200])
#         assert res.status_code == status.HTTP_200_OK
