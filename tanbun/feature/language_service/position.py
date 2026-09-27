"""UIプロトコルに依存しない文書位置の変換."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentPosition:
    """Unicode code point単位の0始まり位置."""

    line: int
    character: int


def position_to_offset(text: str, position: DocumentPosition) -> int:
    """行・文字位置をPython文字列のoffsetへ変換する."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0
    line = max(0, min(position.line, len(lines) - 1))
    line_start = sum(len(value) for value in lines[:line])
    line_text = lines[line].rstrip("\r\n")
    return line_start + max(0, min(position.character, len(line_text)))


def offset_to_position(text: str, offset: int) -> DocumentPosition:
    """Python文字列のoffsetを行・文字位置へ変換する."""
    offset = max(0, min(offset, len(text)))
    before = text[:offset]
    line = before.count("\n")
    line_start = before.rfind("\n") + 1
    return DocumentPosition(line=line, character=offset - line_start)
