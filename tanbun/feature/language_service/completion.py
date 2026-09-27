"""Tanbun文書内の用語補完."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .symbols import RELATION_PATTERN, TermSymbol, extract_term_symbols

_QUOTERM_CONTEXT = re.compile(
    rf"^\s*(?:{RELATION_PATTERN})?`([^`]*)$",
)


@dataclass(frozen=True)
class CompletionResult:
    """補完範囲と候補."""

    start_offset: int
    end_offset: int
    symbols: tuple[TermSymbol, ...]
    closing: str


def complete_terms(text: str, offset: int) -> CompletionResult | None:
    """カーソル位置の用語補完を返す."""
    offset = max(0, min(offset, len(text)))
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end < 0:
        line_end = len(text)
    before = text[line_start:offset]
    after = text[offset:line_end]

    brace = before.rfind("{")
    if brace > before.rfind("}"):
        return CompletionResult(
            start_offset=line_start + brace + 1,
            end_offset=offset,
            symbols=extract_term_symbols(text),
            closing="" if after.startswith("}") else "}",
        )

    quoterm = _QUOTERM_CONTEXT.match(before)
    if quoterm is not None:
        opening = before.rfind("`")
        return CompletionResult(
            start_offset=line_start + opening + 1,
            end_offset=offset,
            symbols=extract_term_symbols(text),
            closing="" if after.startswith("`") else "`",
        )

    return None
