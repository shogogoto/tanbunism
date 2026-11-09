"""見出し操作."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from typing import Final

import networkx as nx
from lark import Token


class HeadingNotFoundError(Exception):
    """見出しが見つからない."""


H_TYPES: Final = [f"H{i}" for i in range(1, 7)]


def include_heading(children: Iterable[Hashable]) -> list:
    """headingのみを取得."""
    return [c for c in children if isinstance(c, Token) and c.type in H_TYPES]


def exclude_heading(children: Iterable[Hashable]) -> list:
    """heading以外を取得."""
    return [c for c in children if not (isinstance(c, Token) and c.type in H_TYPES)]


def get_headings(g: nx.DiGraph) -> set[str]:
    """見出しセット."""
    return {str(n) for n in include_heading(g.nodes)}


def get_heading_path(g: nx.DiGraph, root: Hashable, n: Hashable) -> list[Hashable]:
    """直近の見出しパス."""
    paths = list(nx.all_simple_paths(g.to_undirected(), root, n))
    if len(paths) == 0:
        raise HeadingNotFoundError
    minp = paths[0]
    for p in paths:
        if len(minp) > len(p):
            minp = p
    headings = get_headings(g)
    return [e for e in minp if e in headings]
