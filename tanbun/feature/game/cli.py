"""管理者としてAPI経由でゲーム設定を入出力する。DBへは直接接続しない."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import click
import httpx

from tanbun.config.env import ClientSettings
from tanbun.feature.user.client_auth import auth_header
from tanbun.feature.user.errors import TokenUnsavedError

MAX_FILE_BYTES = 5 * 1024 * 1024
SETTINGS_PATH = "/admin/game/settings-file"


def request_settings(method: str, suffix: str = "", body=None) -> dict:
    """認証済みAPIだけを利用し、書き込みの自動再試行はしない."""
    try:
        headers = auth_header()
    except TokenUnsavedError as error:
        msg = f"{error}。tb user google-sso --no-browser でログインしてください。"
        raise click.ClickException(
            msg,
        ) from error
    try:
        response = httpx.request(
            method,
            ClientSettings().url(SETTINGS_PATH + suffix),
            headers=headers,
            json=body,
            timeout=60,
        )
    except httpx.RequestError as error:
        msg = (
            "APIへの接続に失敗しました。接続先と通信を確認してください。"
            "反映要求後は再実行せず、exportで反映状況を確認してください。"
        )
        raise click.ClickException(
            msg,
        ) from error
    try:
        result = response.json()
    except ValueError as error:
        msg = f"APIがJSON以外を返しました(HTTP {response.status_code})。"
        raise click.ClickException(
            msg,
        ) from error
    if not response.is_success:
        messages = {
            401: "再ログインが必要です: tb user google-sso --no-browser",
            403: "ゲーム設定の操作には管理者権限が必要です。",
            404: "ゲーム設定JSON APIが未反映、または接続先が違います。",
        }
        detail = result.get("detail") if isinstance(result, dict) else None
        message = messages.get(response.status_code) or (
            detail
            if isinstance(detail, str)
            else f"APIエラー(HTTP {response.status_code})"
        )
        raise click.ClickException(message)
    if not isinstance(result, dict):
        msg = "APIの応答形式が不正です。"
        raise click.ClickException(msg)
    return result


def write_document(path: Path, document: dict) -> None:
    """バックアップを含め既存ファイルを上書きしない."""
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    except OSError as error:
        msg = f"{path}へ書き出せません。既存ファイルや出力先を確認してください。"
        raise click.ClickException(
            msg,
        ) from error


@click.group("game")
def game_cli():
    """管理者用ゲーム設定JSON。接続先はKNOWDE_URL、認証はtb userと共通."""


@game_cli.command("export")
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="出力先。省略時は標準出力。既存ファイルは上書きしません。",
)
def export_cmd(output: Path | None):
    """保存済みのゲーム設定全体を書き出す。読み取りのみ."""
    click.echo(f"接続先: {ClientSettings().KNOWDE_URL}", err=True)
    document = request_settings("GET")
    if output:
        write_document(output, document)
        click.echo(f"書き出しました: {output}", err=True)
    else:
        click.echo(json.dumps(document, ensure_ascii=False, indent=2))


@game_cli.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--dry-run",
    is_flag=True,
    help="検証と差分表示だけ行い、保存・バックアップはしません。",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="反映確認を省略します。バックアップは必ず作成します。",
)
@click.option(
    "--backup",
    type=click.Path(dir_okay=False, path_type=Path),
    help="反映前バックアップの出力先。省略時は入力ファイルと同じディレクトリ。",
)
def import_cmd(file: Path, backup: Path | None, *, dry_run: bool, yes: bool):
    """JSONを検証し、バックアップ後にAPIから一括反映する."""
    click.echo(f"接続先: {ClientSettings().KNOWDE_URL}")
    try:
        if file.stat().st_size > MAX_FILE_BYTES:
            msg = "JSONファイルは5MB以下にしてください。"
            raise click.ClickException(msg)
        document = json.loads(file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        msg = "JSONファイルを読み込めません。UTF-8とJSON形式を確認してください。"
        raise click.ClickException(
            msg,
        ) from error
    preview = request_settings("POST", "/preview", {"document": document})
    if not all(key in preview for key in ("changes", "backup", "document", "revision")):
        msg = "プレビュー応答が不正です。反映しません。"
        raise click.ClickException(msg)
    if not preview["changes"]:
        click.echo("変更はありません。")
        return
    for change in preview["changes"]:
        before, after = change["before"], change["after"]
        summary = (
            f"{len(before)}件 → {len(after)}件"
            if isinstance(before, list) and isinstance(after, list)
            else f"{before} → {after}"
        )
        click.echo(f"  {change['section']}: {summary}")
    if dry_run:
        click.echo("検証のみ。DBは変更していません。")
        return
    if not yes:
        click.confirm("この接続先のゲーム設定へ反映しますか?", abort=True)
    path = backup or file.with_name(
        f"{file.stem}.backup-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json",
    )
    write_document(path, preview["backup"])
    click.echo(f"変更前バックアップ: {path}")
    saved = request_settings(
        "PUT",
        body={"revision": preview["revision"], "document": preview["document"]},
    )
    actual = request_settings("GET")
    if actual != saved:
        msg = (
            "反映後の設定が応答と一致しません。exportで確認してください。"
            "自動再反映はしません。"
        )
        raise click.ClickException(
            msg,
        )
    click.echo("ゲーム設定を反映しました(再取得で確認済み)。")
