"""CLI用手続き."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import click
import httpx
from fastapi import status

from tanbun.config import LocalConfig
from tanbun.config.env import Settings
from tanbun.feature.parsing.check.cli import DEFAULT_EXTENSIONS as CHECK_EXTENSIONS
from tanbun.feature.parsing.check.cli import normalize_extension
from tanbun.feature.parsing.check.domain import inspect_document
from tanbun.feature.user.routers.repo.client import AuthGet, auth_header

from . import DEFAULT_UPLOAD_EXTENSIONS


def link_proc() -> None:
    """DBと同期するファイルパスを指定."""
    current = Path.cwd().resolve()
    config = LocalConfig.load()
    config.ANCHOR = current
    config.save()
    click.echo(f"'{current}'を同期元として設定しました")


def sync_proc(
    glob: str | None = None,
    get_client: Callable[..., httpx.Response] | None = None,
    post_client: Callable[..., httpx.Response] | None = None,
    *,
    show_error: bool = True,
    extensions: Iterable[str] = DEFAULT_UPLOAD_EXTENSIONS,
    force: bool = False,
) -> None:
    """Web版と同じく、変更されたファイルを1件ずつ取り込む."""
    config = LocalConfig.load()
    settings = Settings()
    if get_client is None:
        get_client = settings.get
    if post_client is None:
        post_client = httpx.post

    anchor = _configured_anchor(config)
    headers = _authenticated_headers(get_client)

    normalized_extensions = tuple(
        sorted({normalize_extension(extension) for extension in extensions}),
    )
    files = list(_iter_upload_files(anchor, normalized_extensions, glob))
    extension_label = ", ".join(normalized_extensions)
    click.echo(
        f"{anchor.name} 内の {len(files)} 個の対象ファイル ({extension_label})",
    )

    changed_files = [
        path
        for path in files
        if force or not _was_uploaded_unchanged(config, anchor, path)
    ]
    skipped = len(files) - len(changed_files)
    sendable, parse_failed = _preflight(
        anchor,
        changed_files,
        show_error=show_error,
    )
    click.echo(f"送信対象 {len(sendable)}件 / 変更なし {skipped}件")

    succeeded, upload_failed = _upload_files(
        settings,
        config,
        anchor,
        sendable,
        headers,
        post_client=post_client,
    )

    click.echo(
        f"完了: {succeeded}件成功, {skipped}件変更なし, "
        f"{parse_failed + upload_failed}件失敗",
    )
    if parse_failed or upload_failed:
        raise click.exceptions.Exit(1)


def _configured_anchor(config: LocalConfig) -> Path:
    if config.ANCHOR is None or str(config.ANCHOR) == "None":
        click.echo(
            "同期元が未設定です。対象ディレクトリで `tb anchor` を実行してください。",
        )
        raise click.exceptions.Exit(1)
    anchor = Path(config.ANCHOR).expanduser().resolve()
    if not anchor.is_dir():
        click.echo(f"同期元が見つかりません: {anchor}")
        raise click.exceptions.Exit(1)
    return anchor


def _authenticated_headers(
    get_client: Callable[..., httpx.Response],
) -> dict[str, str]:
    try:
        headers = auth_header()
        response = AuthGet(client=get_client).me()
    except httpx.RequestError as exc:
        click.echo(f"サーバーへ接続できません: {exc}")
        raise click.exceptions.Exit(1) from exc
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        click.echo("ログインが必要です。`tb user login EMAIL` を実行してください。")
        raise click.exceptions.Exit(1)
    if not response.is_success:
        click.echo(f"ログイン状態を確認できません: {_response_detail(response)}")
        raise click.exceptions.Exit(1)
    return headers


def _upload_files(
    settings: Settings,
    config: LocalConfig,
    anchor: Path,
    files: list[Path],
    headers: dict[str, str],
    *,
    post_client: Callable[..., httpx.Response],
) -> tuple[int, int]:
    succeeded = 0
    failed = 0
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(anchor).as_posix()
        click.echo(f"[{index}/{len(files)}] {relative}")
        try:
            with path.open("rb") as source:
                response = settings.post(
                    "/resource",
                    headers=headers,
                    files=[("files", (relative, source, "application/octet-stream"))],
                    client=post_client,
                )
        except (OSError, httpx.RequestError) as exc:
            failed += 1
            click.secho(f"  ✗ 送信に失敗しました: {exc}", fg="red", err=True)
            continue
        if not response.is_success:
            failed += 1
            click.secho("  ✗ アップロードできませんでした", fg="red", err=True)
            click.echo(f"    {_response_detail(response)}", err=True)
            continue
        succeeded += 1
        config.UPLOAD_HISTORY[_history_key(anchor, path)] = _fingerprint(path)
        config.save()
        click.secho("  ✓ アップロードに成功しました", fg="green")
    return succeeded, failed


def _iter_upload_files(
    anchor: Path,
    extensions: tuple[str, ...],
    glob: str | None,
) -> Iterable[Path]:
    candidates = anchor.rglob(glob or "*")
    yield from sorted(
        path
        for path in candidates
        if path.is_file() and path.suffix.lower() in extensions
    )


def _preflight(
    anchor: Path,
    files: Iterable[Path],
    *,
    show_error: bool,
) -> tuple[list[Path], int]:
    valid = []
    failed = 0
    for path in files:
        relative = path.relative_to(anchor)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failed += 1
            if show_error:
                click.secho(f"{relative}: 読み取れません: {exc}", fg="red", err=True)
            continue
        inspection = inspect_document(text)
        if inspection.errors:
            failed += 1
            if show_error:
                for issue in inspection.errors:
                    location = issue.source_range
                    click.secho(
                        f"{relative}:{location.line + 1}:"
                        f"{location.start_character + 1}: {issue.message}",
                        fg="red",
                        err=True,
                    )
                    if issue.suggestion:
                        click.secho(
                            f"  修正案: {issue.suggestion}",
                            fg="yellow",
                            err=True,
                        )
            continue
        valid.append(path)
    return valid, failed


def _history_key(anchor: Path, path: Path) -> str:
    return f"{anchor}::{path.relative_to(anchor).as_posix()}"


def _fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _was_uploaded_unchanged(
    config: LocalConfig,
    anchor: Path,
    path: Path,
) -> bool:
    return config.UPLOAD_HISTORY.get(_history_key(anchor, path)) == _fingerprint(path)


def _response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return str(detail.get("message", detail))
    return str(detail)


# check と sync のTanbun拡張子は意図せず乖離させない。
assert set(CHECK_EXTENSIONS).issubset(DEFAULT_UPLOAD_EXTENSIONS)
