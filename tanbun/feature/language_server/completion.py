"""Tanbun文書内の用語補完."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tanbun.feature.parsing.tree2net.lineparse import parse_line

_RELATION_PREFIX = re.compile(
    r"^(?:<->|->|<-|ex\.|xe\.|ref\.|~=|by\.|where\.)\s*",
)
_QUOTERM_CONTEXT = re.compile(
    r"^\s*(?:(?:<->|->|<-|ex\.|xe\.|ref\.|~=|by\.|where\.)\s*)?`([^`]*)$",
)


@dataclass(frozen=True)
class TermSymbol:
    """文書内で参照できる用語名またはalias."""

    label: str
    detail: str


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


def extract_term_symbols(text: str) -> tuple[TermSymbol, ...]:
    """未完成の文書からも用語名とaliasを抽出する."""
    symbols: dict[str, TermSymbol] = {}
    for raw_line in text.splitlines():
        line = _RELATION_PREFIX.sub("", raw_line.strip())
        if not _is_definition_line(line):
            continue

        alias, names, _ = parse_line(line)
        normalized_names = tuple(_normalize_name(name) for name in names)
        normalized_names = tuple(name for name in normalized_names if name)
        for name in normalized_names:
            symbols.setdefault(name, TermSymbol(label=name, detail="用語"))
        if alias:
            detail = "alias"
            if normalized_names:
                detail += f" → {', '.join(normalized_names)}"
            symbols.setdefault(alias, TermSymbol(label=alias, detail=detail))

    return tuple(sorted(symbols.values(), key=lambda symbol: symbol.label.casefold()))


def _is_definition_line(line: str) -> bool:
    if not line or line.startswith(("#", "@", "!", "`", "+++")):
        return False
    if line.startswith("when."):
        return False
    return ":" in line or "|" in line


def _normalize_name(name: str) -> str:
    """複合用語はパーサーのlookup keyと同じ形にする."""
    return name.replace("{", "").replace("}", "").strip()
