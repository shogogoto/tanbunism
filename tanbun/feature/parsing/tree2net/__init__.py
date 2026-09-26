"""parse tree to network."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from tanbun.feature.parsing.primitive.quoterm.domain import add_quoterm_edge
from tanbun.feature.parsing.primitive.term.markresolver import MarkResolver
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysfn import (
    check_duplicated_sentence,
    to_def,
)
from tanbun.feature.parsing.sysnet.sysfn.build_fn import (
    add_resolved_edges,
)
from tanbun.feature.parsing.sysnet.sysnode.merged_def import MergedDef
from tanbun.feature.parsing.tree_parse import get_leaves, parse2tree

from .interpreter import SysNetInterpreter
from .transformer import TSysArg

if TYPE_CHECKING:
    from lark import Tree

    from tanbun.feature.parsing.tree2net.directed_edge import DirectedEdgeCollection


def parse2net_uncached(
    txt: str,
    do_print: bool = False,  # noqa: FBT001 FBT002
) -> SysNet:
    """文からsysnetへ."""
    t = parse2tree(txt, TSysArg())
    if do_print:
        print(t.pretty())  # noqa: T201
        print(t)  # noqa: T201
    si = SysNetInterpreter()
    si.visit(t)
    g = _build_graph(t, si.col)
    g.add_node(si.root)
    return SysNet(root=si.root, g=nx.freeze(g))


@cache
def parse2net(txt: str, do_print: bool = False) -> SysNet:  # noqa: FBT001 FBT002
    """テキストをsysnetへ変換する(キャッシュ付き)."""
    return parse2net_uncached(txt, do_print)


def _build_graph(tree: Tree, col: DirectedEdgeCollection) -> nx.MultiDiGraph:
    g, resolver = _extract_leaves(tree)
    col.set_edges(g)
    add_resolved_edges(g, resolver)
    add_quoterm_edge(g, resolver.lookup.get)
    return g


def _extract_leaves(tree: Tree) -> tuple[nx.MultiDiGraph, MarkResolver]:
    """transformedなASTを処理."""
    leaves = get_leaves(tree)
    check_duplicated_sentence(leaves)
    mdefs, stddefs, mt = MergedDef.create_and_parted(to_def(leaves))
    g = nx.MultiDiGraph()  # 同じノード間で複数エッジを表現できるように
    [md.add_edge(g) for md in mdefs]
    [d.add_edge(g) for d in stddefs]
    return g, MarkResolver.create(mt)


type ParseHandler = Callable[[Path, Exception], None]


def filter_parsable(
    handle_error: ParseHandler | None = None,
) -> Callable[[Iterable[Path]], list[Path]]:
    """パースできるファイルのみを抽出."""

    def can_parse(p: Path) -> bool:
        if not p.is_file():
            return False
        try:
            parse2net(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            if handle_error is not None:
                handle_error(p, e)
            return False
        return True

    def _f(_ps: Iterable[Path]) -> list[Path]:
        return [p for p in _ps if can_parse(p)]

    return _f
