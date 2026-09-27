"""parse tree to network."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
from lark import Token

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.mark import (
    protect_escaped_braces,
    restore_escaped_braces,
)
from tanbun.feature.parsing.primitive.quoterm.domain import add_quoterm_edge
from tanbun.feature.parsing.primitive.template import Template
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.term.markresolver import MarkResolver
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysfn import (
    arg2sentence,
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
    t = parse2tree(protect_escaped_braces(txt), TSysArg())
    if do_print:
        print(t.pretty())  # noqa: T201
        print(t)  # noqa: T201
    si = SysNetInterpreter()
    si.visit(t)
    g = _build_graph(t, si.col)
    g.add_node(si.root)
    restored_graph = _restore_escaped_braces_in_graph(g)
    restored_root = _restore_node(si.root)
    return SysNet(root=restored_root, g=nx.freeze(restored_graph))


@cache
def parse2net(txt: str, do_print: bool = False) -> SysNet:  # noqa: FBT001 FBT002
    """テキストをsysnetへ変換する(キャッシュ付き)."""
    return parse2net_uncached(txt, do_print)


def _build_graph(tree: Tree, col: DirectedEdgeCollection) -> nx.MultiDiGraph:
    g, resolver = _extract_leaves(tree, col)
    col.set_edges(g)
    add_resolved_edges(g, resolver)
    add_quoterm_edge(g, resolver.lookup.get)
    return g


def _extract_leaves(
    tree: Tree,
    col: DirectedEdgeCollection,
) -> tuple[nx.MultiDiGraph, MarkResolver]:
    """transformedなASTを処理."""
    leaves = get_leaves(tree)
    reusable_locations = [
        arg2sentence(edge.nodes[-1]) for edge in col.values if edge.t is EdgeType.WHERE
    ]
    check_duplicated_sentence(leaves, reusable=reusable_locations)
    mdefs, stddefs, mt = MergedDef.create_and_parted(to_def(leaves))
    g = nx.MultiDiGraph()  # 同じノード間で複数エッジを表現できるように
    [md.add_edge(g) for md in mdefs]
    [d.add_edge(g) for d in stddefs]
    return g, MarkResolver.create(mt)


def _restore_escaped_braces_in_graph(
    graph: nx.MultiDiGraph,
) -> nx.MultiDiGraph:
    """参照解決後に、ノード内のエスケープされた波括弧を戻す."""
    mapping = {
        node: restored
        for node in graph.nodes
        if (restored := _restore_node(node)) != node
    }
    return nx.relabel_nodes(graph, mapping, copy=True)


def _restore_node(node):
    """グラフノードの型を保ったまま、波括弧だけを復元する."""
    if isinstance(node, Token):
        restored = restore_escaped_braces(str(node))
        return node if restored == str(node) else Token(node.type, restored)
    if isinstance(node, str):
        return restore_escaped_braces(node)
    if isinstance(node, Term):
        return Term(
            names=tuple(restore_escaped_braces(name) for name in node.names),
            alias=(
                restore_escaped_braces(node.alias) if node.alias is not None else None
            ),
        )
    if isinstance(node, Template):
        return node.model_copy(
            update={
                "name": restore_escaped_braces(node.name),
                "args": tuple(restore_escaped_braces(arg) for arg in node.args),
                "form": restore_escaped_braces(node.form),
            },
        )
    if isinstance(node, Duplicable) and isinstance(node.n, str):
        restored = restore_escaped_braces(node.n)
        return node if restored == node.n else node.model_copy(update={"n": restored})
    return node


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
