"""行の解析."""

from __future__ import annotations

import pytest

from . import parse_line


@pytest.mark.parametrize(
    ("line", "alias", "names", "sentence"),
    [
        ("a:", None, ["a"], None),
        ("a: bbb", None, ["a"], "bbb"),
        ("a\uff1a bbb", None, ["a"], "bbb"),
        ("a1, a2: bbb", None, ["a1", "a2"], "bbb"),
        ("P |a:", "P", ["a"], None),
        ("P |a: bbb", "P", ["a"], "bbb"),
        ("P |a\uff1a bbb", "P", ["a"], "bbb"),
        ("P |a1, a2: bbb", "P", ["a1", "a2"], "bbb"),
        ("bbb", None, [], "bbb"),
        ("P |bbb", "P", [], "bbb"),
    ],
)
def test_parse_oneline(
    line: str,
    alias: str | None,
    names: list[str],
    sentence: str | None,
) -> None:
    """改行ありで一行とみなす."""
    assert parse_line(line) == (alias, names, sentence)
