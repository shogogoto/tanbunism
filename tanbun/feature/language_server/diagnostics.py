"""パーサーの例外をエディタ向け診断に変換する."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lark import UnexpectedInput

from tanbun.feature.parsing.tree2net import parse2net_uncached
from tanbun.feature.parsing.tree_parse.errors import KnSyntaxError

KN_SYNTAX_ERROR_ARG_COUNT = 3


@dataclass(frozen=True)
class SourceRange:
    """ソース上の0始まりの範囲."""

    line: int
    start_character: int
    end_character: int


@dataclass(frozen=True)
class TanbunDiagnostic:
    """LSPに依存しないTanbunの診断."""

    code: str
    message: str
    source_range: SourceRange


def diagnose(text: str) -> list[TanbunDiagnostic]:
    """文書全体を検査する."""
    try:
        parse2net_uncached(text)
    except Exception as exc:  # noqa: BLE001 - parser errors are the diagnostics
        return [_exception_to_diagnostic(text, exc)]
    return []


def _exception_to_diagnostic(text: str, exc: Exception) -> TanbunDiagnostic:
    line, character, length = _locate_error(text, exc)
    return TanbunDiagnostic(
        code=exc.__class__.__name__,
        message=_diagnostic_message(exc),
        source_range=SourceRange(
            line=line,
            start_character=character,
            end_character=character + max(1, length),
        ),
    )


def _locate_error(text: str, exc: Exception) -> tuple[int, int, int]:
    line = getattr(exc, "line", None)
    column = getattr(exc, "column", None)
    if isinstance(line, int) and isinstance(column, int):
        return max(0, line - 1), max(0, column - 1), 1

    if (
        isinstance(exc, KnSyntaxError)
        and len(exc.args) >= KN_SYNTAX_ERROR_ARG_COUNT
    ):
        _, line, column = exc.args[:KN_SYNTAX_ERROR_ARG_COUNT]
        return max(0, int(line) - 1), max(0, int(column) - 1), 1

    message = str(exc)
    match = re.search(r"at line (\d+)", message)
    if match:
        return max(0, int(match.group(1)) - 1), 0, 1

    quoted = re.match(r"'([^']+)'", message)
    if quoted:
        value = quoted.group(1)
        marked = f"{{{value}}}"
        needle = marked if marked in text else value
        offset = text.rfind(needle)
        if offset >= 0:
            line, character = _offset_to_position(text, offset)
            return line, character, len(needle)

    return 0, 0, 1


def _offset_to_position(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    return before.count("\n"), len(before.rsplit("\n", maxsplit=1)[-1])


def _diagnostic_message(exc: Exception) -> str:
    if isinstance(exc, UnexpectedInput):
        expected = sorted(getattr(exc, "expected", ()))
        first_line = str(exc).splitlines()[0]
        if expected:
            return f"{first_line}\nExpected: {', '.join(expected)}"
        return first_line
    return f"[{exc.__class__.__name__}] {exc}"
