"""不完全なTanbun文書からも作れる用語索引."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tanbun.feature.parsing.issue import SourceRange
from tanbun.feature.parsing.tree2net.lineparse import DEF_SEPS, parse_line

RELATION_PATTERN = r"(?:<->|->|<-|ex\.|xe\.|ref\.|~=|by\.|where\.)\s*"
RELATION_PREFIX = re.compile(rf"^{RELATION_PATTERN}")


@dataclass(frozen=True)
class TermSymbol:
    """文書内で参照できる用語名またはaliasと、その定義位置."""

    label: str
    detail: str
    source_range: SourceRange


def extract_term_symbols(text: str) -> tuple[TermSymbol, ...]:
    """文書全体のparseに失敗しても用語名とaliasを抽出する."""
    symbols: dict[str, TermSymbol] = {}
    for line_number, raw_line in enumerate(text.splitlines()):
        for symbol in _extract_line_symbols(raw_line, line_number):
            symbols.setdefault(symbol.label, symbol)
    return tuple(sorted(symbols.values(), key=lambda symbol: symbol.label.casefold()))


def _extract_line_symbols(  # noqa: PLR0914 - ranges require lexical positions
    raw_line: str,
    line_number: int,
) -> tuple[TermSymbol, ...]:
    content_start = len(raw_line) - len(raw_line.lstrip())
    content = raw_line[content_start:]
    relation = RELATION_PREFIX.match(content)
    if relation is not None:
        content_start += relation.end()
        content = content[relation.end() :]
    if not _is_definition_line(content):
        return ()

    alias, names, _ = parse_line(content)
    separator_index = _first_index(content, DEF_SEPS)
    definition_end = len(content) if separator_index is None else separator_index
    pipe_index = content.find("|")
    names_start = pipe_index + 1 if pipe_index >= 0 else 0
    symbols = []

    normalized_names = tuple(_normalize_name(name) for name in names)
    normalized_names = tuple(name for name in normalized_names if name)
    if alias and pipe_index >= 0:
        start, end = _trimmed_range(content, 0, pipe_index)
        detail = "alias"
        if normalized_names:
            detail += f" → {', '.join(normalized_names)}"
        symbols.append(
            TermSymbol(
                label=alias,
                detail=detail,
                source_range=SourceRange(
                    line=line_number,
                    start_character=content_start + start,
                    end_character=content_start + end,
                ),
            ),
        )

    cursor = names_start
    name_segments = content[names_start:definition_end].split(",")
    for raw_name, name in zip(name_segments, names, strict=False):
        segment_end = cursor + len(raw_name)
        start, end = _trimmed_range(content, cursor, segment_end)
        normalized = _normalize_name(name)
        if normalized:
            symbols.append(
                TermSymbol(
                    label=normalized,
                    detail="用語",
                    source_range=SourceRange(
                        line=line_number,
                        start_character=content_start + start,
                        end_character=content_start + end,
                    ),
                ),
            )
        cursor = segment_end + 1
    return tuple(symbols)


def _is_definition_line(line: str) -> bool:
    if not line or line.startswith(("#", "@", "!", "`", "+++")):
        return False
    if line.startswith("when."):
        return False
    return any(separator in line for separator in DEF_SEPS) or "|" in line


def _first_index(text: str, candidates: tuple[str, ...]) -> int | None:
    positions = [text.index(candidate) for candidate in candidates if candidate in text]
    return min(positions) if positions else None


def _trimmed_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _normalize_name(name: str) -> str:
    """複合用語はパーサーのlookup keyと同じ形にする."""
    return name.replace("{", "").replace("}", "").strip()
