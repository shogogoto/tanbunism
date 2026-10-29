"""ゲーム設定CLIはAPIだけを利用し、確認前にDBを変更しない."""

import json
import os
import subprocess  # noqa: S404 - fixed Python command for startup regression tests
import sys

import httpx
import pytest
from click.testing import CliRunner

from tanbun.feature.user import client_auth
from tanbun.feature.user.errors import TokenUnsavedError

from . import cli

BEFORE = {"format": "tanbunism.game-settings", "version": 1, "balance": {"base_hp": 10}}
AFTER = {**BEFORE, "balance": {"base_hp": 20}}
PREVIEW = {
    "revision": "revision-before",
    "backup": BEFORE,
    "document": AFTER,
    "changes": [{"section": "balance.base_hp", "before": 10, "after": 20}],
}


@pytest.fixture
def api(monkeypatch):
    """ネットワークをモックし、認証付きのリクエストと送信順だけを記録する."""
    monkeypatch.setenv("KNOWDE_URL", "https://api.example.test")
    monkeypatch.setattr(
        client_auth,
        "auth_header",
        lambda: {"Authorization": "Bearer cli-test"},
    )
    state = {"calls": [], "saved": False, "backup": None}

    def request(method, url, **kwargs):
        assert url.startswith("https://api.example.test/admin/game/settings-file")
        assert kwargs["headers"]["Authorization"] == "Bearer cli-test"
        state["calls"].append((method, kwargs.get("json")))
        if method == "POST":
            return httpx.Response(200, json=PREVIEW)
        if method == "PUT":
            if state["backup"]:
                assert json.loads(state["backup"].read_text()) == BEFORE
            state["saved"] = True
            return httpx.Response(200, json=AFTER)
        return httpx.Response(200, json=AFTER if state["saved"] else BEFORE)

    monkeypatch.setattr(httpx, "request", request)
    return state


@pytest.fixture
def source(tmp_path):
    """取り込みファイルはユーザーの作業ディレクトリに置く."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(AFTER), encoding="utf-8")
    return path


def test_export_stdout_is_json_and_does_not_write(api):
    """接続先はstderrへ表示し、標準出力をJSONとして利用できる."""
    result = CliRunner().invoke(cli.game_cli, ["export"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == BEFORE
    assert "https://api.example.test" in result.stderr
    assert api["calls"] == [("GET", None)]


def test_export_does_not_overwrite_existing_files(api, tmp_path):
    """既存ファイルを誤って消さない."""
    path = tmp_path / "backup.json"
    path.write_text("keep", encoding="utf-8")
    result = CliRunner().invoke(cli.game_cli, ["export", "-o", str(path)])
    assert result.exit_code == 1
    assert path.read_text() == "keep"
    assert not api["saved"]


def test_dry_run_only_previews(api, source):
    """検証だけでは保存もバックアップ作成もしない."""
    result = CliRunner().invoke(cli.game_cli, ["import", str(source), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "balance.base_hp: 10 → 20" in result.output
    assert [method for method, _ in api["calls"]] == ["POST"]
    assert list(source.parent.iterdir()) == [source]


def test_explicit_import_backs_up_before_put_and_checks_readback(api, source):
    """入力とは別のバックアップを作ってから、確認したrevisionで保存する."""
    backup = source.parent / "before.json"
    api["backup"] = backup
    result = CliRunner().invoke(
        cli.game_cli,
        ["import", str(source), "--backup", str(backup)],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert api["calls"] == [
        ("POST", {"document": AFTER}),
        ("PUT", {"revision": "revision-before", "document": AFTER}),
        ("GET", None),
    ]
    assert json.loads(backup.read_text()) == BEFORE
    assert "再取得で確認済み" in result.output


def test_yes_still_creates_backup(api, source):
    """非対話モードでもバックアップは省略できない."""
    result = CliRunner().invoke(cli.game_cli, ["import", str(source), "--yes"])
    assert result.exit_code == 0, result.output
    backups = list(source.parent.glob("settings.backup-*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == BEFORE


@pytest.mark.parametrize("cancel", ["n\n", ""])
def test_cancel_never_writes(api, source, cancel):
    """否定やEOFではバックアップもDB書き込みもしない."""
    result = CliRunner().invoke(cli.game_cli, ["import", str(source)], input=cancel)
    assert result.exit_code == 1
    assert not api["saved"]
    assert list(source.parent.iterdir()) == [source]


def test_existing_backup_blocks_apply(api, source):
    """退避先に書けない場合はDBを先に更新しない."""
    result = CliRunner().invoke(
        cli.game_cli,
        ["import", str(source), "--yes", "--backup", str(source)],
    )
    assert result.exit_code == 1
    assert not api["saved"]
    assert json.loads(source.read_text()) == AFTER


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 503])
def test_api_errors_are_nonzero_and_do_not_print_credentials(api, monkeypatch, status):
    """権限や競合エラーを成功扱いにしない."""
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *_args, **_kwargs: httpx.Response(
            status,
            json={"detail": "settings error"},
        ),
    )
    result = CliRunner().invoke(cli.game_cli, ["export"])
    assert result.exit_code == 1
    assert "cli-test" not in result.output


def test_malformed_json_never_calls_api(api, source):
    """不正ファイルを送信しない."""
    source.write_text("{", encoding="utf-8")
    result = CliRunner().invoke(cli.game_cli, ["import", str(source), "--yes"])
    assert result.exit_code == 1
    assert api["calls"] == []


def test_missing_credentials_gives_login_guidance(api, monkeypatch):
    """認証なしでAPIを呼ばない."""

    def missing():
        msg = "not logged in"
        raise TokenUnsavedError(msg)

    monkeypatch.setattr(client_auth, "auth_header", missing)
    result = CliRunner().invoke(cli.game_cli, ["export"])
    assert result.exit_code == 1
    assert "google-sso --no-browser" in result.output
    assert api["calls"] == []


def test_ambiguous_put_failure_is_not_retried(api, source, monkeypatch):
    """タイムアウト後は勝手に再送せず、退避ファイルを残す."""
    request = httpx.request

    def timeout(method, url, **kwargs):
        if method == "PUT":
            api["calls"].append((method, kwargs["json"]))
            msg = "timeout"
            raise httpx.ReadTimeout(msg)
        return request(method, url, **kwargs)

    monkeypatch.setattr(httpx, "request", timeout)
    result = CliRunner().invoke(cli.game_cli, ["import", str(source), "--yes"])
    assert result.exit_code == 1
    assert "再実行せず" in result.output
    assert [method for method, _ in api["calls"]] == ["POST", "PUT"]
    assert len(list(source.parent.glob("settings.backup-*.json"))) == 1


@pytest.mark.parametrize(
    "arguments",
    [["game", "--help"], ["user", "google-sso", "--help"]],
)
def test_cli_starts_outside_repository_without_server_secrets(tmp_path, arguments):
    """別ディレクトリからはDBやGoogleクライアント秘密鍵なしで起動できる."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"NEO4J_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "KNOWDE_URL"}
    }
    result = subprocess.run(  # noqa: S603 - arguments are literal test parameters
        [sys.executable, "-c", "from tanbun.cli import cli; cli()", *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
