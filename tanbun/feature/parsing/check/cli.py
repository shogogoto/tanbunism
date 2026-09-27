"""Tanbun文書を一括検査するCLI."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import click

from tanbun.feature.parsing.check.domain import inspect_document
from tanbun.feature.parsing.issue import ParseIssue

DEFAULT_EXTENSIONS = ("tb", "kn")


@click.command("check")
@click.argument(
    "path",
    type=click.Path(exists=True, path_type=Path),
    default=Path(),
)
@click.option(
    "--extension",
    "extensions",
    multiple=True,
    default=DEFAULT_EXTENSIONS,
    show_default=True,
    help="ディレクトリ検査の対象拡張子。複数回指定できます。",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="エラーの位置、該当行、原因、修正案を表示します。",
)
def check_cmd(
    path: Path,
    extensions: tuple[str, ...],
    *,
    verbose: bool,
) -> None:
    """PATHのTanbun文書をDBへ接続せず検査する."""
    files = list(_iter_files(path, extensions))
    error_count = 0

    for source_path in files:
        try:
            text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            error_count += 1
            display_path = _display_path(source_path, path)
            if verbose:
                _echo_read_error(display_path, exc)
            else:
                click.echo(display_path, err=True)
            continue

        inspection = inspect_document(text)
        if inspection.issue is None:
            continue
        error_count += 1
        display_path = _display_path(source_path, path)
        if verbose:
            _echo_issue(display_path, text, inspection.issue)
        else:
            click.echo(display_path, err=True)

    _echo_summary(len(files), error_count)
    if error_count:
        raise click.exceptions.Exit(1)


def _iter_files(path: Path, extensions: tuple[str, ...]) -> Iterable[Path]:
    if path.is_file():
        yield path
        return

    suffixes = {_normalize_extension(extension) for extension in extensions}
    yield from sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() in suffixes
    )


def _normalize_extension(extension: str) -> str:
    normalized = extension.lower().strip()
    return normalized if normalized.startswith(".") else f".{normalized}"


def _display_path(source_path: Path, root: Path) -> Path:
    if root.is_file():
        return source_path
    try:
        return source_path.relative_to(root)
    except ValueError:
        return source_path


def _echo_issue(path: Path, text: str, issue: ParseIssue) -> None:
    source_range = issue.source_range
    line_number = source_range.line + 1
    column_number = source_range.start_character + 1
    click.secho(
        f"{path}:{line_number}:{column_number}: {issue.message}",
        fg="red",
        err=True,
    )

    line = _line_at(text, source_range.line)
    if line:
        number_width = len(str(line_number))
        click.echo(f"{' ' * number_width} |", err=True)
        click.echo(f"{line_number} | {line}", err=True)
        marker_length = max(
            1,
            min(
                source_range.end_character - source_range.start_character,
                len(line) - source_range.start_character,
            ),
        )
        click.secho(
            f"{' ' * number_width} | "
            f"{' ' * source_range.start_character}{'^' * marker_length}",
            fg="red",
            err=True,
        )
    if issue.suggestion is not None:
        click.secho(f"  修正案: {issue.suggestion}", fg="yellow", err=True)
    click.echo(err=True)


def _echo_read_error(path: Path, exc: Exception) -> None:
    click.secho(f"{path}: ファイルを読み取れません: {exc}", fg="red", err=True)
    click.echo(err=True)


def _echo_summary(file_count: int, error_count: int) -> None:
    valid_count = file_count - error_count
    message = f"{file_count}ファイルを検査: {valid_count}正常, {error_count}エラー"
    click.secho(
        message,
        fg="red" if error_count else "green",
        err=True,
    )


def _line_at(text: str, line_index: int) -> str:
    lines = text.splitlines()
    if 0 <= line_index < len(lines):
        return lines[line_index]
    return ""
