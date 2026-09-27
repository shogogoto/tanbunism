"""パース例外を利用者向けの問題表現へ変換する."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from edtf import EDTFParseException
from lark import UnexpectedCharacters, UnexpectedEOF, UnexpectedInput

from tanbun.feature.parsing.primitive.mark.errors import MarkContainsMarkError
from tanbun.feature.parsing.primitive.quoterm.errors import QuotermNotFoundError
from tanbun.feature.parsing.primitive.term.errors import (
    AliasContainsMarkError,
    TermConflictError,
)
from tanbun.feature.parsing.primitive.time.errors import ParseWhenError
from tanbun.feature.parsing.tree2net.errors import OrphanRelationError
from tanbun.feature.parsing.tree2net.lineparse import parse_line
from tanbun.feature.parsing.tree_parse.errors import (
    AttachDetailError,
    HeadingMismatchError,
    KnSyntaxError,
    MissingIndentError,
    MissingTopHeadingError,
    UndedentError,
)

KN_SYNTAX_ERROR_ARG_COUNT = 3
PARSE_WHEN_ERROR_ARG_COUNT = 2
MIN_DUPLICATE_DEFINITIONS = 2
FULLWIDTH_COLON = "\uff1a"
FULLWIDTH_SPACE = "\u3000"


class ParseIssueSeverity(StrEnum):
    """パース問題の重大度."""

    ERROR = "error"
    WARNING = "warning"


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
    severity: ParseIssueSeverity = ParseIssueSeverity.ERROR

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


def lint_parse_style(text: str) -> tuple[ParseIssue, ...]:
    """パースは可能だが正規形ではない記法を検出する."""
    issues = []
    for line_index, line in enumerate(text.splitlines()):
        separator_index = _definition_separator_index(line)
        if separator_index is None or line[separator_index] != FULLWIDTH_COLON:
            continue
        issues.append(
            ParseIssue(
                code="noncanonical-definition-separator",
                message="定義区切りに全角コロンが使われています。",
                suggestion="半角コロン「:」へ変更",
                source_range=SourceRange(
                    line=line_index,
                    start_character=separator_index,
                    end_character=separator_index + 1,
                ),
                severity=ParseIssueSeverity.WARNING,
            ),
        )
    return tuple(issues)


def _locate_error(  # noqa: C901, PLR0911, PLR0912 - one branch per error
    text: str,
    exc: Exception,
) -> SourceRange:
    if isinstance(exc, UndedentError):
        whitespace_range = _locate_non_ascii_indent(text, str(exc))
        if whitespace_range is not None:
            return whitespace_range
    if isinstance(exc, OrphanRelationError):
        return _locate_orphan_relation(text, exc.target)
    if isinstance(exc, QuotermNotFoundError):
        quoterm_range = _locate_quoterm(text, str(exc))
        if quoterm_range is not None:
            return quoterm_range
    if isinstance(exc, (EDTFParseException, ParseWhenError)):
        time_range = _locate_invalid_time(text, exc)
        if time_range is not None:
            return time_range
    if isinstance(exc, TermConflictError):
        definition_ranges = _locate_term_definitions(text, exc)
        if definition_ranges:
            return definition_ranges[-1]
    if isinstance(exc, MarkContainsMarkError):
        mark_range = _locate_nested_mark(text, exc)
        if mark_range is not None:
            return mark_range
    if isinstance(exc, AliasContainsMarkError) and exc.alias:
        alias_range = _locate_value(text, exc.alias)
        if alias_range is not None:
            return alias_range
    return _locate_generic_error(text, exc)


def _locate_non_ascii_indent(text: str, message: str) -> SourceRange | None:
    """インデントエラー以前で最後に現れる全角スペースを探す."""
    reported = re.search(r"line (\d+)", message)
    last_line = int(reported.group(1)) - 1 if reported is not None else None
    found = None
    for line_number, line in enumerate(text.splitlines()):
        if last_line is not None and line_number > last_line:
            break
        match = re.match(rf"^[ \t]*({FULLWIDTH_SPACE})", line)
        if match is not None:
            start, end = match.span(1)
            found = SourceRange(line_number, start, end)
    return found


def _locate_value(text: str, value: str) -> SourceRange | None:
    """例外が保持する入力値そのものをソース上で探す."""
    offset = text.find(value)
    if offset < 0:
        return None
    line, character = _offset_to_position(text, offset)
    return SourceRange(
        line=line,
        start_character=character,
        end_character=character + len(value),
    )


def _locate_invalid_time(
    text: str,
    original: EDTFParseException | ParseWhenError,
) -> SourceRange | None:
    """内部で正規化された日時エラーを元のwhen行へ戻す."""
    original_value = _invalid_time_value(original)
    exact = _locate_value(text, original_value) if original_value else None
    if exact is not None:
        return exact

    from tanbun.feature.parsing.primitive.time import parse_when  # noqa: PLC0415

    pattern = re.compile(r"^\s*(?:when\.|@published)\s+(.+?)\s*$")
    for line_number, line in enumerate(text.splitlines()):
        match = pattern.match(line)
        if match is None:
            continue
        value = match.group(1)
        try:
            parse_when(value)
        except (EDTFParseException, ParseWhenError) as candidate:
            if type(candidate) is not type(original):
                continue
            candidate_value = _invalid_time_value(candidate)
            if original_value and candidate_value != original_value:
                continue
            start, end = match.span(1)
            return SourceRange(line_number, start, end)
        except Exception:  # noqa: BLE001, S112 - 別種の日時エラーは無関係
            continue
    return None


def _invalid_time_value(exc: EDTFParseException | ParseWhenError) -> str | None:
    """日時例外が保持する、利用者が入力した値を返す."""
    if isinstance(exc, EDTFParseException):
        return exc.input_string
    if len(exc.args) >= PARSE_WHEN_ERROR_ARG_COUNT and isinstance(exc.args[1], str):
        return exc.args[1]
    return None


def _locate_generic_error(text: str, exc: Exception) -> SourceRange:
    """例外の一般的な位置情報やメッセージからソース範囲を得る."""
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

    quoted = re.search(r"'([^']+)'", message)
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


def _explain_known_error(  # noqa: C901, PLR0911, PLR0912 - one branch per error
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
        return _explain_undent(text, source_range)
    if isinstance(exc, OrphanRelationError):
        return (
            "orphan-relation",
            "関係行に接続元の単文がありません。",
            "直前の単文の配下になるよう、この行をさらにインデント",
        )
    if isinstance(exc, QuotermNotFoundError):
        name = _first_quoted_value(str(exc))
        return (
            "undefined-quoterm",
            f"引用用語「{name}」が定義されていません。",
            f"「{name}: ...」と定義するか、既存の用語名へ変更",
        )
    if isinstance(exc, TermConflictError):
        names = _term_names_from_conflict(exc)
        name = "、".join(names)
        definitions = _locate_term_definitions(text, exc)
        first_location = ""
        if len(definitions) >= MIN_DUPLICATE_DEFINITIONS:
            first_location = f" 最初の定義は{definitions[0].line + 1}行目です。"
        return (
            "duplicate-term",
            f"用語「{name}」が複数回定義されています。{first_location}".rstrip(),
            "どちらかの定義を削除するか、1つの定義に統合",
        )
    if isinstance(exc, MarkContainsMarkError):
        return (
            "nested-term-reference",
            "用語参照の波括弧「{ }」は入れ子にできません。",
            "記号として書く場合は「{{」「}}」のように2つ重ねる",
        )
    if isinstance(exc, AliasContainsMarkError):
        return (
            "invalid-alias",
            f"alias「{exc.alias}」に用語参照の波括弧は使用できません。",
            "aliasから「{」「}」を削除するか、波括弧を本文側へ移動",
        )
    if isinstance(exc, (EDTFParseException, ParseWhenError)):
        line = _line_at(text, source_range.line)
        source_value = line[source_range.start_character : source_range.end_character]
        normalized = _invalid_time_value(exc) or source_value
        value = source_value or normalized
        suggestion = "対応している日時または「開始 ~ 終了」形式の期間へ変更"
        if re.fullmatch(r"\d{3}X-\d{4}", normalized):
            start = normalized[:3] + "0"
            end = normalized[-4:]
            suggestion = f"期間を表すなら「{start} ~ {end}」へ変更"
        if re.fullmatch(r"-?\d+\s+(?:EARLY|MID|LATE)", normalized):
            suggestion = (
                "EARLY / MID / LATE は「19C EARLY」のような世紀表記にだけ"
                "使用できます。年代を表す場合は「1970 ~ 1973」のような"
                "具体的な期間へ変更"
            )
        return (
            "invalid-time-expression",
            f"日時・期間「{value}」を解釈できません。",
            suggestion,
        )
    return None


def _explain_undent(
    text: str,
    source_range: SourceRange,
) -> tuple[str, str, str]:
    line = _line_at(text, source_range.line)
    marked = line[source_range.start_character : source_range.end_character]
    if marked == FULLWIDTH_SPACE:
        return (
            "non-ascii-indent",
            "行頭のインデントに全角スペースが混ざっています。",
            "全角スペースを半角スペースへ置き換え、周囲と深さを揃える",
        )
    return (
        "invalid-indent",
        "インデントの深さが前後の行と一致していません。",
        "前後の行に合わせて行頭のスペース数を変更",
    )


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


def _locate_quoterm(text: str, message: str) -> SourceRange | None:
    name = _first_quoted_value(message)
    needle = f"`{name}`"
    offset = text.rfind(needle)
    if offset < 0:
        return None
    line, character = _offset_to_position(text, offset + 1)
    return SourceRange(
        line=line,
        start_character=character,
        end_character=character + len(name),
    )


def _first_quoted_value(message: str) -> str:
    match = re.search(r"'([^']+)'", message)
    return match.group(1) if match is not None else message


def _term_names_from_conflict(exc: TermConflictError) -> tuple[str, ...]:
    """構造化された用語名を優先し、旧形式の例外にも対応する."""
    if exc.names:
        return exc.names
    return (_first_quoted_value(str(exc)),)


def _locate_term_definitions(
    text: str,
    exc: TermConflictError,
) -> list[SourceRange]:
    """同名用語の定義行だけを探す.

    ``{name}`` の参照は重複定義の原因ではないため候補から除く。
    """
    conflict_names = _term_names_from_conflict(exc)
    primary_name = conflict_names[0]
    found = []
    for line_index, line in enumerate(text.splitlines()):
        try:
            _, names, _ = parse_line(line)
        except ValueError:
            continue
        if not set(conflict_names).issubset(names):
            continue
        separator_index = _definition_separator_index(line)
        if separator_index is None:
            continue
        definition = line[:separator_index]
        name_start = definition.find(primary_name)
        if name_start < 0:
            continue
        found.append(
            SourceRange(
                line=line_index,
                start_character=name_start,
                end_character=name_start + len(primary_name),
            ),
        )
    return found


def _locate_nested_mark(
    text: str,
    exc: MarkContainsMarkError,
) -> SourceRange | None:
    """入れ子になった波括弧を、解析対象の行へ戻す."""
    if exc.source is None:
        return None
    source_offset = text.rfind(exc.source)
    if source_offset < 0:
        return None
    nested = re.search(r"\{\{|\}\}", exc.source)
    relative_start = nested.start() if nested is not None else 0
    relative_end = nested.end() if nested is not None else len(exc.source)
    start = source_offset + relative_start
    end = source_offset + relative_end
    line, character = _offset_to_position(text, start)
    end_line, end_character = _offset_to_position(text, end)
    if end_line != line:
        end_character = max(character + 1, _line_length(text, line))
    return SourceRange(line, character, end_character)


def _definition_separator_index(line: str) -> int | None:
    stripped = line.lstrip()
    ignored_prefixes = ("#", "!", "@", "when.", "`", "+++")
    if not stripped or stripped.startswith(ignored_prefixes):
        return None
    indexes = [
        index
        for separator in (":", FULLWIDTH_COLON)
        if (index := line.find(separator)) >= 0
    ]
    return min(indexes) if indexes else None
