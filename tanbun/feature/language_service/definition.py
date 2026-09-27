"""用語参照から文書内の定義位置を探す."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from tanbun.feature.parsing.issue import SourceRange

from .symbols import extract_term_symbols

_REFERENCE = re.compile(r"\{([^{}]+)\}|`([^`]+)`")


@dataclass(frozen=True)
class SourceDocument:
    """索引対象のローカル文書."""

    uri: str
    text: str


@dataclass(frozen=True)
class DefinitionLocation:
    """用語が定義された文書と位置."""

    uri: str
    source_range: SourceRange


def find_definition(text: str, offset: int) -> SourceRange | None:
    """カーソル下の埋め込み用語またはquotermの定義位置を返す."""
    offset = max(0, min(offset, len(text)))
    reference = _reference_at(text, offset)
    if reference is None:
        return None
    definitions = {
        symbol.label: symbol.source_range for symbol in extract_term_symbols(text)
    }
    return definitions.get(reference)


def find_definitions(
    current: SourceDocument,
    offset: int,
    documents: Iterable[SourceDocument],
) -> tuple[DefinitionLocation, ...]:
    """現在の文書を優先して複数のローカル文書から定義を探す."""
    reference = _reference_at(current.text, offset)
    if reference is None:
        return ()

    ordered = [current]
    ordered.extend(document for document in documents if document.uri != current.uri)
    locations = []
    seen = set()
    for document in ordered:
        for symbol in extract_term_symbols(document.text):
            key = (document.uri, symbol.source_range)
            if symbol.label != reference or key in seen:
                continue
            locations.append(DefinitionLocation(document.uri, symbol.source_range))
            seen.add(key)
        if locations and document.uri == current.uri:
            return tuple(locations)
    return tuple(locations)


def _reference_at(text: str, offset: int) -> str | None:
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end < 0:
        line_end = len(text)
    for match in _REFERENCE.finditer(text, line_start, line_end):
        value_group = 1 if match.group(1) is not None else 2
        start, end = match.span(value_group)
        if start <= offset <= end:
            return match.group(value_group).strip()
    return None
