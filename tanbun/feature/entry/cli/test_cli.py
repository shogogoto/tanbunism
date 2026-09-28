"""CLI test."""

from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from tanbun.api import root_router
from tanbun.config import LocalConfig
from tanbun.feature.entry.cli.proc import (
    _fingerprint,
    _history_key,
    _iter_upload_files,
    _was_uploaded_unchanged,
    sync_proc,
)
from tanbun.feature.user.routers.repo.client import AuthPost, auth_header


@patch("tanbun.config.LocalConfig.load")
def test_sync_cmd(mock_load, tmp_path: Path):
    """Sync command test."""
    client = TestClient(root_router())
    ap = AuthPost(client=client.post)
    d = {"email": "test@test.com", "password": "test"}
    ap.register(**d)
    res = ap.login(**d)
    mock_load.return_value.ANCHOR = tmp_path
    mock_load.return_value.CREDENTIALS = res.json()

    # tmp_pathに適当なファイルとディレクトリを作成
    (tmp_path / "test_dir").mkdir()
    (tmp_path / "test_dir" / "test_file.kn").write_text("""
        # title1
    """)
    sync_proc("**/*.kn", get_client=client.get, post_client=client.post)

    h = auth_header()
    res = client.get("/namespace", headers=h)
    assert res.is_success

    g = res.json()["g"]
    assert len(g["edges"]) > 0


def test_default_upload_files_include_tb_and_web_extensions(tmp_path: Path) -> None:
    """Web版と同じ既定拡張子に.tbも加えて再帰検索する."""
    nested = tmp_path / "nested"
    nested.mkdir()
    expected = {
        tmp_path / "memo.txt",
        tmp_path / "memo.md",
        nested / "memo.kn",
        nested / "memo.tb",
    }
    for path in expected | {tmp_path / "ignored.rst"}:
        path.write_text("# title\n", encoding="utf-8")

    actual = set(
        _iter_upload_files(
            tmp_path,
            (".txt", ".md", ".kn", ".tb"),
            None,
        ),
    )

    assert actual == expected


def test_successful_upload_fingerprint_skips_unchanged_file(tmp_path: Path) -> None:
    """Web版と同様にサイズと更新時刻が同じ送信済みファイルを省略する."""
    path = tmp_path / "memo.tb"
    path.write_text("# title\n", encoding="utf-8")
    key = _history_key(tmp_path, path)
    config = LocalConfig(
        UPLOAD_HISTORY={key: _fingerprint(path)},
    )

    assert _was_uploaded_unchanged(config, tmp_path, path)

    config.UPLOAD_HISTORY[key] = (0, 0)
    assert not _was_uploaded_unchanged(config, tmp_path, path)


def test_sync_uploads_directly_then_skips_unchanged_file(
    tmp_path: Path,
) -> None:
    """成功履歴を保存し、2回目はWeb版同様に送信を省略する."""
    path = tmp_path / "memo.tb"
    path.write_text("# title\n  body\n", encoding="utf-8")
    config = LocalConfig(
        ANCHOR=tmp_path,
        CREDENTIALS={"access_token": "token", "token_type": "bearer"},
    )
    uploaded_paths = []

    def get_client(*_args, **_kwargs) -> httpx.Response:
        return httpx.Response(status_code=200, json={})

    def post_client(*_args, **kwargs) -> httpx.Response:
        uploaded_paths.append(kwargs["files"][0][1][0])
        return httpx.Response(status_code=200, json={})

    with (
        patch("tanbun.config.LocalConfig.load", return_value=config),
        patch.object(LocalConfig, "save"),
    ):
        sync_proc(get_client=get_client, post_client=post_client)
        sync_proc(get_client=get_client, post_client=post_client)

    assert uploaded_paths == ["memo.tb"]


def test_sync_skips_parse_for_unchanged_uploaded_file(tmp_path: Path) -> None:
    """送信済みで未変更なら、重い事前parseも繰り返さない."""
    path = tmp_path / "memo.tb"
    path.write_text("これはparseできないが送信済み", encoding="utf-8")
    key = _history_key(tmp_path, path)
    config = LocalConfig(
        ANCHOR=tmp_path,
        CREDENTIALS={"access_token": "token", "token_type": "bearer"},
        UPLOAD_HISTORY={key: _fingerprint(path)},
    )

    def get_client(*_args, **_kwargs) -> httpx.Response:
        return httpx.Response(status_code=200, json={})

    with patch("tanbun.config.LocalConfig.load", return_value=config):
        sync_proc(
            get_client=get_client,
            post_client=lambda *_args, **_kwargs: httpx.Response(status_code=200),
        )
