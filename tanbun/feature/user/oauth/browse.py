"""Google SSO for local and SSH terminals, without printing credentials."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path

import click
import httpx

from tanbun.config import LocalConfig
from tanbun.config.env import ClientSettings
from tanbun.feature.user import PREFIX_USER


def read_credentials(text: str) -> dict:
    """ログイン結果だけを受け取り、HTMLや認証エラーを保存しない."""
    try:
        value = json.loads(text)
    except ValueError as error:
        msg = "ログイン結果のJSONを貼り付けてください。認証コードやURLではありません。"
        raise click.ClickException(
            msg,
        ) from error
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("access_token"), str)
        or not value["access_token"].strip()
        or str(value.get("token_type", "")).lower() != "bearer"
    ):
        msg = "access_tokenとtoken_type(bearer)を含むJSONが必要です。"
        raise click.ClickException(
            msg,
        )
    return {"access_token": value["access_token"], "token_type": "bearer"}


def credential_text(
    settings: ClientSettings,
    *,
    open_browser: bool,
    token_file: Path | None,
) -> str:
    """SSHでも認証URLを表示し、ブラウザの結果を端末またはファイルで受け取る."""
    if token_file:
        text = token_file.read_text(encoding="utf-8-sig")
    else:
        response = httpx.get(settings.url("/google/authorize"), timeout=30)
        if not response.is_success:
            msg = (
                f"認証URLを取得できませんでした(HTTP {response.status_code})。"
                "接続先を確認してください。"
            )
            raise click.ClickException(
                msg,
            )
        try:
            url = response.json().get("authorization_url")
        except (ValueError, AttributeError) as error:
            msg = "認証APIの応答が不正です。接続先を確認してください。"
            raise click.ClickException(
                msg,
            ) from error
        if not isinstance(url, str) or not url.startswith(
            "https://accounts.google.com/",
        ):
            msg = "Googleの認証URLを取得できませんでした。"
            raise click.ClickException(msg)
        click.echo(
            "次のURLを手元のブラウザで開いてログインしてください(SSHのポート転送は不要です)。",
        )
        click.echo(url)
        if open_browser:
            try:
                opened = webbrowser.open(url)
            except (webbrowser.Error, OSError):
                opened = False
            if not opened:
                click.echo(
                    "ブラウザを開けませんでした。上のURLを手元のブラウザで開いてください。",
                )
        click.echo(
            "認証後に表示されるJSONを、この端末へ貼り付けてください。入力内容は表示しません。",
        )
        text = click.prompt("ログイン結果JSON (1行)", hide_input=True)
    return text


def browse_for_sso(
    *,
    open_browser: bool = True,
    token_file: Path | None = None,
) -> bool:
    """認証URLを表示し、手元のブラウザで得た結果を隠して入力する."""
    settings = ClientSettings()
    click.echo(f"接続先: {settings.KNOWDE_URL}")
    try:
        text = credential_text(
            settings,
            open_browser=open_browser,
            token_file=token_file,
        )
        credentials = read_credentials(text)
        verified = httpx.get(
            settings.url(f"{PREFIX_USER}/me"),
            headers={"Authorization": f"Bearer {credentials['access_token']}"},
            timeout=30,
        )
    except httpx.RequestError as error:
        msg = "認証APIへの接続に失敗しました。KNOWDE_URLと通信を確認してください。"
        raise click.ClickException(
            msg,
        ) from error
    except (OSError, UnicodeError) as error:
        msg = "トークンファイルをUTF-8で読み込めませんでした。"
        raise click.ClickException(
            msg,
        ) from error
    if not verified.is_success:
        msg = (
            f"ログインを確認できませんでした(HTTP {verified.status_code})。"
            "認証情報は変更していません。"
        )
        raise click.ClickException(
            msg,
        )
    config = LocalConfig.load()
    config.CREDENTIALS = credentials
    config.CREDENTIALS_URL = settings.KNOWDE_URL.rstrip("/")
    try:
        config.save()
    except OSError as error:
        msg = "認証情報を保存できませんでした。設定ファイルの権限を確認してください。"
        raise click.ClickException(msg) from error
    click.echo("認証情報を保存しました。")
    return True
