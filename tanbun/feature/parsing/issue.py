"""パース例外を利用者向けの問題表現へ変換する."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lark import UnexpectedCharacters, UnexpectedEOF, UnexpectedInput

from tanbun.feature.parsing.tree2net.errors import OrphanRelationError
from tanbun.feature.parsing.tree_parse.errors import (
    AttachDetailError,
    HeadingMismatchError,
    KnSyntaxError,
    MissingIndentError,
    MissingTopHeadingError,
    UndedentError,
)

KN_SYNTAX_ERROR_ARG_COUNT = 3


@dataclass(frozen=True)
class SourceRange:
    """ソース上の0始まりの範囲."""

    line: int
    start_character: int
    end_character: int


@dataclass(frozen=True)
class ParseIssue:
    """Webとエディタで共有する、利用者向けのパース問題."""

    code: str
    message: str
    source_range: SourceRange
    suggestion: str | None = None

    def display_message(self) -> str:
        """人が読む表示用メッセージを返す."""
        location = f"{self.source_range.line + 1}行目: "
        message = f"{location}{self.message}"
        if self.suggestion is not None:
            return f"{message}\n修正案: {self.suggestion}"
        return message


def exception_to_parse_issue(text: str, exc: Exception) -> ParseIssue:
    """パーサー内部の例外を共通の問題表現へ変換する."""
    source_range = _locate_error(text, exc)
    code, message, suggestion = _explain_error(text, exc, source_range)
    return ParseIssue(
        code=code,
        message=message,
        suggestion=suggestion,
        source_range=source_range,
    )


def _locate_error(text: str, exc: Exception) -> SourceRange:
    if isinstance(exc, OrphanRelationError):
        return _locate_orphan_relation(text, exc.target)

    line = getattr(exc, "line", None)
    column = getattr(exc, "column", None)
    if isinstance(line, int) and isinstance(column, int):
        line_index = max(0, line - 1)
        character = max(0, column - 1)
        return SourceRange(
            line=line_index,
            start_character=character,
            end_character=max(character + 1, _line_length(text, line_index)),
        )

    if isinstance(exc, KnSyntaxError) and len(exc.args) >= KN_SYNTAX_ERROR_ARG_COUNT:
        _, line, column = exc.args[:KN_SYNTAX_ERROR_ARG_COUNT]
        line_index = max(0, int(line) - 1)
        character = max(0, int(column) - 1)
        return SourceRange(
            line=line_index,
            start_character=character,
            end_character=max(character + 1, _line_length(text, line_index)),
        )

    message = str(exc)
    match = re.search(r"at line (\d+)", message)
    if match:
        line_index = max(0, int(match.group(1)) - 1)
        return (
            SourceRange(0, 0, 1)
            if not text
            else SourceRange(
                line_index,
                0,
                max(1, _line_length(text, line_index)),
            )
        )

    quoted = re.match(r"'([^']+)'", message)
    if quoted:
        value = quoted.group(1)
        marked = f"{{{value}}}"
        needle = marked if marked in text else value
        offset = text.rfind(needle)
        if offset >= 0:
            line_index, character = _offset_to_position(text, offset)
            return SourceRange(
                line=line_index,
                start_character=character,
                end_character=character + len(needle),
            )

    return SourceRange(line=0, start_character=0, end_character=1)


def _explain_error(
    text: str,
    exc: Exception,
    source_range: SourceRange,
) -> tuple[str, str, str | None]:
    known = _explain_known_error(text, exc, source_range)
    if known is not None:
        return known
    if isinstance(exc, UnexpectedInput):
        return _explain_unexpected_input(text, exc, source_range)
    return "invalid-document", str(exc), None


def _explain_known_error(  # noqa: PLR0911 - each known error has its own guidance
    text: str,
    exc: Exception,
    source_range: SourceRange,
) -> tuple[str, str, str | None] | None:
    """独自パーサーの既知エラーを説明する."""
    if isinstance(exc, MissingIndentError):
        return (
            "missing-indent",
            "見出し配下の本文にインデントがありません。",
            _indent_suggestion(text, source_range.line),
        )
    if isinstance(exc, MissingTopHeadingError):
        return (
            "missing-title",
            "文書の先頭にタイトルがありません。",
            "先頭に「# タイトル」を追加",
        )
    if isinstance(exc, HeadingMismatchError):
        return (
            "heading-level-mismatch",
            "見出しレベルが飛んでいます。",
            "直前の見出しより1段だけ深い見出しに変更",
        )
    if isinstance(exc, AttachDetailError):
        return (
            "invalid-attachment",
            "付加情報の配下には本文を置けません。",
            "本文を付加情報と同じ階層へ移動",
        )
    if isinstance(exc, UndedentError):
        return (
            "invalid-indent",
            "インデントの深さが前後の行と一致していません。",
            "前後の行に合わせて行頭のスペース数を変更",
        )
    if isinstance(exc, OrphanRelationError):
        return (
            "orphan-relation",
            "関係行に接続元の単文がありません。",
            "直前の単文の配下になるよう、この行をさらにインデント",
        )
    return None


def _explain_unexpected_input(
    text: str,
    exc: UnexpectedInput,
    source_range: SourceRange,
) -> tuple[str, str, str | None]:
    line_text = _line_at(text, source_range.line)
    if _looks_like_unindented_body(text, source_range.line, line_text):
        return (
            "missing-indent",
            "本文が見出しと同じ深さにあります。",
            _indent_suggestion(text, source_range.line),
        )
    if source_range.line == 0 and not line_text.startswith("# "):
        return (
            "missing-title",
            "文書は「# タイトル」から始めてください。",
            None,
        )
    heading_suggestion = _heading_level_suggestion(line_text, exc)
    if heading_suggestion is not None:
        return (
            "heading-level-mismatch",
            "見出しレベルが飛んでいます。",
            heading_suggestion,
        )
    if isinstance(exc, UnexpectedEOF):
        return (
            "incomplete-document",
            "文書が途中で終わっています。",
            "閉じ記号や、続きが必要な関係が残っていないか確認",
        )
    if isinstance(exc, UnexpectedCharacters):
        return (
            "invalid-character",
            "この位置では使用できない文字があります。",
            "強調された位置の記号やインデントを確認",
        )

    expected = sorted(getattr(exc, "expected", ()))
    expectation = _describe_expected(expected)
    message = "現在の記法ではこの行を読み取れません。"
    if expectation:
        message = f"{message} ここには{expectation}を書けます。"
    return "invalid-syntax", message, None


def _heading_level_suggestion(
    line_text: str,
    exc: UnexpectedInput,
) -> str | None:
    """飛び越した見出しに、直せる最大レベルを案内する."""
    match = re.match(r"^(#+)\s*", line_text)
    if match is None:
        return None
    expected_levels = [
        int(token[1:])
        for token in getattr(exc, "expected", ())
        if re.fullmatch(r"H[1-6]", token)
    ]
    if not expected_levels:
        return None
    actual_level = len(match.group(1))
    allowed_level = max(expected_levels)
    if actual_level <= allowed_level:
        return None
    title = line_text[actual_level:].lstrip()
    return f"「{'#' * allowed_level} {title}」へ変更"


def _looks_like_unindented_body(
    text: str,
    line_index: int,
    line_text: str,
) -> bool:
    if line_index <= 0 or not line_text or line_text[0].isspace():
        return False
    if line_text.startswith("#"):
        return False
    return any(line.startswith("#") for line in text.splitlines()[:line_index])


def _indent_suggestion(text: str, line_index: int) -> str:
    line_text = _line_at(text, line_index).lstrip()
    return f"行頭にスペースを追加 (例: 「  {line_text}」)"


def _describe_expected(tokens: list[str]) -> str:
    labels = {
        "H1": "# 見出し",
        "H2": "## 見出し",
        "H3": "### 見出し",
        "H4": "#### 見出し",
        "H5": "##### 見出し",
        "H6": "###### 見出し",
        "_INDENT": "インデントした本文",
        "_NL": "空行",
        "$END": "ファイル末尾",
    }
    values = [labels[token] for token in tokens if token in labels]
    return "、".join(dict.fromkeys(values))


def _line_at(text: str, line_index: int) -> str:
    lines = text.splitlines()
    if 0 <= line_index < len(lines):
        return lines[line_index]
    return ""


def _line_length(text: str, line_index: int) -> int:
    return len(_line_at(text, line_index))


def _offset_to_position(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    return before.count("\n"), len(before.rsplit("\n", maxsplit=1)[-1])


def _locate_orphan_relation(text: str, target: str) -> SourceRange:
    relation_prefixes = ("<->", "->", "<-", "ex.", "xe.", "ref.", "~=", "by.", "where.")
    for line_index, line in enumerate(text.splitlines()):
        stripped = line.lstrip()
        if target in line and stripped.startswith(relation_prefixes):
            start = len(line) - len(stripped)
            return SourceRange(
                line=line_index,
                start_character=start,
                end_character=len(line),
            )
    return SourceRange(line=0, start_character=0, end_character=1)
