"""SSHのSSO操作と、認証情報を表示・誤保存しないことの回帰テスト."""
# ruff: noqa: PLR2004, S105

import json
from unittest.mock import Mock

import httpx
import pytest
from click.testing import CliRunner

from tanbun import config
from tanbun.cli import cli
from tanbun.feature.user import PREFIX_USER
from tanbun.feature.user.cli import user_cli
from tanbun.feature.user.errors import TokenUnsavedError
from tanbun.feature.user.routers.repo.client import auth_header

from . import browse

TOKEN = "private-test-credential"
RESULT = {"access_token": TOKEN, "token_type": "bearer"}
URL = "https://accounts.google.com/o/oauth2/auth?state=test-state"


@pytest.fixture
def auth(monkeypatch, tmp_path):
    """本物の設定ファイル、ブラウザ、認証APIにはアクセスしない."""
    file = tmp_path / "credentials.json"
    monkeypatch.setattr(config, "_F", file)
    monkeypatch.setenv("KNOWDE_URL", "https://api.example.test")
    opened = Mock(return_value=True)
    monkeypatch.setattr(browse.webbrowser, "open", opened)

    def get(url, **kwargs):
        assert url.startswith("https://api.example.test/")
        if url.endswith("/google/authorize"):
            return httpx.Response(200, json={"authorization_url": URL})
        assert url.endswith(f"{PREFIX_USER}/me")
        assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"id": "user"})

    monkeypatch.setattr(browse.httpx, "get", get)
    return file, opened


def test_ssh_prints_url_and_hides_pasted_token(auth):
    """手元でURLを開け、貼り付けた認証情報はログに残らない."""
    file, opened = auth
    result = CliRunner().invoke(
        user_cli,
        ["google-sso", "--no-browser"],
        input=json.dumps(RESULT) + "\n",
    )
    assert result.exit_code == 0, result.output
    assert URL in result.output
    assert TOKEN not in result.output
    opened.assert_not_called()
    saved = json.loads(file.read_text())
    assert saved["CREDENTIALS"] == RESULT
    assert saved["CREDENTIALS_URL"] == "https://api.example.test"
    assert file.stat().st_mode & 0o777 == 0o600


def test_browser_failure_keeps_manual_login_available(auth):
    """ブラウザがないSSHホストでも認証結果を受け取れる."""
    file, opened = auth
    opened.side_effect = browse.webbrowser.Error("no browser")
    result = CliRunner().invoke(
        user_cli,
        ["google-sso"],
        input=json.dumps(RESULT) + "\n",
    )
    assert result.exit_code == 0, result.output
    assert URL in result.output
    assert "ブラウザを開けません" in result.output
    assert file.exists()
    assert TOKEN not in result.output


def test_token_file_accepts_multiline_json_without_echo(auth, tmp_path):
    """複数行のブラウザ応答もファイル経由で利用できる."""
    _, opened = auth
    path = tmp_path / "login.json"
    path.write_text(json.dumps(RESULT, indent=2), encoding="utf-8")
    result = CliRunner().invoke(user_cli, ["google-sso", "--token-file", str(path)])
    assert result.exit_code == 0, result.output
    opened.assert_not_called()
    assert TOKEN not in result.output


@pytest.mark.parametrize(
    "text",
    [
        "invalid",
        "{}",
        "[]",
        '{"error":"login failed"}',
        '{"access_token":"","token_type":"bearer"}',
    ],
)
def test_invalid_input_does_not_replace_existing_credentials(auth, text):
    """JSONでない応答や認証エラーで、保存済み認証を壊さない."""
    file, _ = auth
    original = config.LocalConfig(CREDENTIALS=RESULT)
    original.save()
    before = file.read_bytes()
    result = CliRunner().invoke(
        user_cli,
        ["google-sso", "--no-browser"],
        input=text + "\n",
    )
    assert result.exit_code == 1
    assert file.read_bytes() == before
    assert TOKEN not in result.output


def test_failed_verification_and_connection_do_not_save(auth, monkeypatch):
    """古いトークンや違う接続先のトークンは保存前に拒否する."""
    file, _ = auth
    request = browse.httpx.get

    def reject(url, **kwargs):
        return (
            httpx.Response(401, json={})
            if url.endswith(f"{PREFIX_USER}/me")
            else request(url, **kwargs)
        )

    monkeypatch.setattr(browse.httpx, "get", reject)
    result = CliRunner().invoke(
        user_cli,
        ["google-sso", "--no-browser"],
        input=json.dumps(RESULT) + "\n",
    )
    assert result.exit_code == 1
    assert "認証情報は変更していません" in result.output
    assert not file.exists()


def test_credentials_are_not_sent_to_another_server(auth, monkeypatch):
    """本番とローカルを移動したときに、保存済み認証を取り違えない."""
    result = CliRunner().invoke(
        user_cli,
        ["google-sso", "--no-browser"],
        input=json.dumps(RESULT) + "\n",
    )
    assert result.exit_code == 0
    monkeypatch.setenv("KNOWDE_URL", "https://other.example.test")
    with pytest.raises(TokenUnsavedError, match="接続先が違います"):
        auth_header()


def test_config_output_redacts_tokens_and_legacy_credentials_remain_readable(auth):
    """既存ファイルを使えるまま、tb configで秘密を表示しない."""
    config.LocalConfig(CREDENTIALS=RESULT).save()
    assert auth_header()["Authorization"] == f"Bearer {TOKEN}"
    result = CliRunner().invoke(cli, ["config"])
    assert result.exit_code == 0
    assert TOKEN not in result.output
    assert "<redacted>" in result.output
