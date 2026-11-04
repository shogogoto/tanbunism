"""用語の宣言と参照を、ファイルを書き換えず編集案として返す."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tanbun.feature.parsing.issue import SourceRange

from .definition import DefinitionLocation, SourceDocument
from .position import offset_to_position
from .references import find_references, reference_locations
from .symbols import TermSymbol, extract_term_occurrences

_EMBEDDED = re.compile(r"\{([^{}]+)\}")


class RenameError(ValueError):
    """安全に変更できない名前または対象."""


@dataclass(frozen=True)
class RenameTarget:
    """カーソル下の変更対象と入力欄の初期値."""

    name: str
    source_range: SourceRange


@dataclass(frozen=True)
class RenameEdit(DefinitionLocation):
    """一つの宣言または参照の置換."""

    new_text: str


def _source(text: str, span: SourceRange) -> str:
    return text.splitlines()[span.line][span.start_character : span.end_character]


def _trimmed(text: str, span: SourceRange) -> SourceRange:
    raw = _source(text, span)
    return SourceRange(
        span.line,
        span.start_character + len(raw) - len(raw.lstrip()),
        span.end_character - (len(raw) - len(raw.rstrip())),
    )


def _editable(text: str, span: SourceRange) -> bool:
    return not text.splitlines()[span.line].lstrip().startswith(("#", "@", "!", "+++"))


def prepare_rename(
    current: SourceDocument,
    offset: int,
    documents: tuple[SourceDocument, ...] = (),
) -> RenameTarget | None:
    """定義がある用語だけを対象にし、括弧と周囲の空白を選択範囲に含めない."""
    position = offset_to_position(current.text, offset)
    candidates = []
    for location in find_references(current, offset, include_declaration=True):
        span = _trimmed(current.text, location.source_range)
        if (
            span.line == position.line
            and span.start_character <= position.character < span.end_character
            and _editable(current.text, span)
        ):
            candidates.append(span)
    if not candidates:
        return None
    span = min(candidates, key=lambda item: item.end_character - item.start_character)
    name = _source(current.text, span)
    if any(char in name for char in "{}`"):
        return None  # 複合用語全体を平文で上書きして構造を壊さない。
    declarations = [
        (document, symbol)
        for document in (current, *documents)
        for symbol in extract_term_occurrences(document.text)
        if symbol.label == name
    ]
    if not declarations or any(
        "{" in _source(document.text, symbol.source_range)
        for document, symbol in declarations
    ):
        return None
    return RenameTarget(name, span)


def rename_term(
    current: SourceDocument,
    offset: int,
    new_name: str,
    documents: tuple[SourceDocument, ...] = (),
) -> tuple[RenameEdit, ...]:
    """同じ表記の定義・参照をworkspace全体で変更し、複合用語の名前も追従する."""
    target = prepare_rename(current, offset, documents)
    if target is None:
        msg = "定義のある用語名・alias・用語参照の上で実行してください。"
        raise RenameError(msg)
    _validate_name(new_name)
    if new_name == target.name:
        return ()
    ordered = {document.uri: document for document in documents}
    ordered[current.uri] = current
    declarations = [
        (document, symbol)
        for document in ordered.values()
        for symbol in extract_term_occurrences(document.text)
    ]
    if any(
        symbol.label == target.name
        and symbol.detail.startswith("alias")
        and any(char.isspace() for char in new_name)
        for _, symbol in declarations
    ):
        msg = "aliasには空白を含められません。"
        raise RenameError(msg)
    names = {target.name: new_name}
    _expand_composed_names(names, declarations)
    if len(set(names.values())) != len(names):
        msg = "変更により複数の用語名が同じ名前になるため変更できません。"
        raise RenameError(msg)
    for old, new in names.items():
        if any(
            symbol.label == new and symbol.label != old for _, symbol in declarations
        ):
            msg = f"変更先「{new}」は既に用語名またはaliasとして定義されています。"
            raise RenameError(msg)
    edits = {
        (edit.uri, edit.source_range): edit
        for document in ordered.values()
        for edit in _document_edits(document, names)
    }
    return tuple(
        sorted(
            edits.values(),
            key=lambda edit: (
                edit.uri,
                edit.source_range.line,
                edit.source_range.start_character,
            ),
        ),
    )


def _document_edits(
    document: SourceDocument,
    names: dict[str, str],
) -> list[RenameEdit]:
    edits = []
    for old, new in names.items():
        for location in reference_locations(document, old):
            span = _trimmed(document.text, location.source_range)
            if _editable(document.text, span):
                edits.append(RenameEdit(document.uri, span, new))
    edits.extend(
        RenameEdit(document.uri, symbol.source_range, names[symbol.label])
        for symbol in extract_term_occurrences(document.text)
        if symbol.label in names
        and _source(document.text, symbol.source_range) == symbol.label
    )
    return edits


def _validate_name(name: str) -> None:
    if (
        not name
        or name != name.strip()
        or any(char in name for char in "{}|,:\uff1a`\r\n\t")
        or not name.isprintable()
    ):
        msg = "用語名には空の文字列・前後の空白・改行・構文の区切り文字を使えません。"
        raise RenameError(msg)
    symbols = extract_term_occurrences(f"  {name}: rename")
    if len(symbols) != 1 or symbols[0].label != name:
        msg = "その名前はTanbunの用語名として使えません。"
        raise RenameError(msg)


def _expand_composed_names(
    names: dict[str, str],
    declarations: list[tuple[SourceDocument, TermSymbol]],
) -> None:
    """AをBにしたとき、prefix{A}とその参照prefixAも一緒に変更する."""
    for _ in range(len(declarations) + 1):
        additions = {}
        for document, symbol in declarations:
            raw = _source(document.text, symbol.source_range)
            if "{" not in raw:
                continue
            updated = _EMBEDDED.sub(
                lambda match: names.get(match[1].strip(), match[1]),
                raw,
            )
            label = updated.replace("{", "").replace("}", "").strip()
            if label == symbol.label:
                continue
            previous = additions.get(symbol.label)
            if previous is not None and previous != label:
                msg = "複合用語の名前を一意に変更できません。"
                raise RenameError(msg)
            additions[symbol.label] = label
        if all(names.get(old) == new for old, new in additions.items()):
            return
        names.update(additions)
    msg = "複合用語の参照が循環しているため変更できません。"
    raise RenameError(msg)
