"""Tanbun文書内の用語とメタデータの補完."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .symbols import RELATION_PATTERN, TermSymbol, extract_term_symbols

_QUOTERM_CONTEXT = re.compile(
    rf"^\s*(?:{RELATION_PATTERN})?`([^`]*)$",
)
_METADATA_CONTEXT = re.compile(r"^[ \t]*@([a-zA-Z]*)$")


@dataclass(frozen=True)
class MetadataField:
    """メタデータのキーと説明."""

    label: str
    detail: str


_METADATA_FIELDS = (
    MetadataField("author", "著者"),
    MetadataField("published", "第一出版日"),
    MetadataField("url", "出典URL"),
)


@dataclass(frozen=True)
class CompletionResult:
    """補完範囲と候補."""

    start_offset: int
    end_offset: int
    symbols: tuple[TermSymbol | MetadataField, ...]
    closing: str


def complete_metadata(text: str, offset: int) -> CompletionResult | None:
    """行頭の@に続くメタデータキーを補完する."""
    offset = max(0, min(offset, len(text)))
    line_start = text.rfind("\n", 0, offset) + 1
    match = _METADATA_CONTEXT.fullmatch(text[line_start:offset])
    if match is None:
        return None

    # キーの途中で補完した場合も、後ろのキー文字列だけを置き換える。
    suffix = re.match(r"[a-zA-Z]*", text[offset:])
    end_offset = offset + len(suffix[0])
    following = text[end_offset : end_offset + 1]
    return CompletionResult(
        start_offset=line_start + match.start(1),
        end_offset=end_offset,
        symbols=_METADATA_FIELDS,
        closing="" if following in {" ", "\t", ":"} else " ",
    )


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
