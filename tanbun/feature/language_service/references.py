"""Tanbun用語の参照検索."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from tanbun.feature.parsing.issue import SourceRange

from .definition import DefinitionLocation, SourceDocument
from .position import offset_to_position
from .symbols import extract_term_symbols

_REFERENCE = re.compile(r"\{([^{}]+)\}|`([^`]+)`")


class ReferenceKind(StrEnum):
    """ソース上の用語参照の種類."""

    EMBEDDED_TERM = "embedded-term"
    QUOTERM = "quoterm"
    DEFINITION = "definition"


@dataclass(frozen=True)
class ReferenceLocation(DefinitionLocation):
    """用語が参照された文書、位置、記法."""

    kind: ReferenceKind


@dataclass(frozen=True)
class ReferenceGroup:
    """同じ記法の参照をまとめたWeb表示向け単位."""

    kind: ReferenceKind
    locations: tuple[ReferenceLocation, ...]


def find_references(
    current: SourceDocument,
    offset: int,
    documents: tuple[SourceDocument, ...] = (),
    *,
    include_declaration: bool = False,
) -> tuple[ReferenceLocation, ...]:
    """カーソル下の用語を参照する場所をローカル文書群から探す."""
    term = _term_at(current.text, offset)
    if term is None:
        return ()

    ordered = (current, *(item for item in documents if item.uri != current.uri))
    locations = []
    for document in ordered:
        locations.extend(_reference_locations(document, term))
        if include_declaration:
            locations.extend(_definition_locations(document, term))
    return _deduplicate(locations)


def group_references(
    locations: tuple[ReferenceLocation, ...],
) -> tuple[ReferenceGroup, ...]:
    """参照を埋め込み、引用用語、定義の順でグループ化する."""
    return tuple(
        ReferenceGroup(
            kind=kind,
            locations=tuple(
                location for location in locations if location.kind is kind
            ),
        )
        for kind in ReferenceKind
        if any(location.kind is kind for location in locations)
    )


def _deduplicate(
    locations: list[ReferenceLocation],
) -> tuple[ReferenceLocation, ...]:
    unique = {}
    for location in locations:
        key = (location.uri, location.source_range, location.kind)
        unique.setdefault(key, location)
    return tuple(unique.values())


def _term_at(text: str, offset: int) -> str | None:
    offset = max(0, min(offset, len(text)))
    for match in _REFERENCE.finditer(text):
        group = 1 if match.group(1) is not None else 2
        start, end = match.span(group)
        if start <= offset <= end:
            return match.group(group).strip()

    position = offset_to_position(text, offset)
    for symbol in extract_term_symbols(text):
        source_range = symbol.source_range
        if (
            source_range.line == position.line
            and source_range.start_character
            <= position.character
            <= source_range.end_character
        ):
            return symbol.label
    return None


def _reference_locations(
    document: SourceDocument,
    term: str,
) -> list[ReferenceLocation]:
    locations = []
    for line_number, line in enumerate(document.text.splitlines()):
        for match in _REFERENCE.finditer(line):
            group = 1 if match.group(1) is not None else 2
            if match.group(group).strip() != term:
                continue
            start, end = match.span(group)
            locations.append(
                ReferenceLocation(
                    uri=document.uri,
                    source_range=SourceRange(line_number, start, end),
                    kind=(
                        ReferenceKind.EMBEDDED_TERM
                        if group == 1
                        else ReferenceKind.QUOTERM
                    ),
                ),
            )
    return locations


def _definition_locations(
    document: SourceDocument,
    term: str,
) -> list[ReferenceLocation]:
    return [
        ReferenceLocation(
            uri=document.uri,
            source_range=symbol.source_range,
            kind=ReferenceKind.DEFINITION,
        )
        for symbol in extract_term_symbols(document.text)
        if symbol.label == term
    ]
