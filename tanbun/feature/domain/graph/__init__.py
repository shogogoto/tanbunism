"""networkx関連."""

from __future__ import annotations

from collections.abc import Hashable
from pprint import pp
from typing import TYPE_CHECKING, Any

import networkx as nx

from .errors import GraphCycleError

if TYPE_CHECKING:
    from .edge_type import EdgeType
    from .types import Accessor, Edges


def nxprint(g: nx.DiGraph, detail: bool = False) -> None:  # noqa: FBT001 FBT002
    """確認用."""
    print()  # noqa: T201
    nx.write_network_text(g)
    if detail:
        pp(nx.to_dict_of_dicts(g))


def to_nested(g: nx.DiGraph, start: Hashable, f: Accessor) -> dict:
    """到達可能なDAGを入れ子にする。合流する枝は残し、循環は拒否する.

    start自身はキーに含めない。入力グラフ・返却辞書はキャッシュしない。
    """
    result = {}
    active = {start}
    stack = [(start, result, iter(f(g, start)))]
    while stack:
        node, children, neighbors = stack[-1]
        try:
            child = next(neighbors)
        except StopIteration:
            active.remove(node)
            stack.pop()
            continue
        if child in active:
            msg = f"Cannot convert a cyclic graph to a nested mapping: {child!r}"
            raise GraphCycleError(msg)
        nested = {}
        children[child] = nested
        active.add(child)
        stack.append((child, nested, iter(f(g, child))))
    return result


def to_nodes(g: nx.DiGraph, start: Hashable, f: Accessor) -> list[Hashable]:
    """startを含む到達点を深さ優先の初出順で一度ずつ取得する.

    循環・深い階層でも終了し、再帰呼び出しの戻り値を混入させない。
    """
    nodes = [start]
    visited = {start}
    stack = [iter(f(g, start))]
    while stack:
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if node in visited:
            continue
        visited.add(node)
        nodes.append(node)
        stack.append(iter(f(g, node)))
    return nodes


def select_edge_attr(g: nx.DiGraph, name: str, *values: Any) -> nx.DiGraph:
    """ある属性のエッジのみを抽出する関数を返す."""
    sub = g.__class__()
    for e in g.edges(data=True):
        u, v, attr = e
        a = {k: v for k, v in attr.items() if k == name and v in values}
        if any(a):
            sub.add_edge(u, v, **a)
    return sub


def _to_paths(g: nx.DiGraph, pairs: Edges) -> list[list[Hashable]]:
    paths = []
    for u, v in pairs:
        try:
            p = list(nx.shortest_path(g, u, v))
            paths.append(p)
        except nx.NetworkXNoPath:
            continue
    return paths


def to_leaves(g: nx.DiGraph, *ts: Any) -> list[Hashable]:
    """最先端を取得."""
    sub = select_edge_attr(g, "type", *ts)
    return [n for n in sub.nodes if sub.out_degree(n) == 0 and sub.in_degree(n) != 0]


def to_roots(g: nx.DiGraph, *ts: Any) -> list[Hashable]:
    """根を取得."""
    sub = select_edge_attr(g, "type", *ts)
    return [n for n in sub.nodes if sub.in_degree(n) == 0 and sub.out_degree(n) != 0]


def leaf_paths(g: nx.DiGraph, tgt: Hashable, t: Any) -> list[list[Hashable]]:
    """入力ノードから特定の関係を遡って依存先がないノードまでのパスを返す."""
    sub = select_edge_attr(g, "type", t)
    pairs = [(tgt, lv) for lv in to_leaves(g, t)]
    return _to_paths(sub, pairs)


def root_paths(g: nx.DiGraph, tgt: Hashable, t: Any) -> list[list[Hashable]]:
    """入力ノードから特定の関係を遡って依存元がないノードまでのパスを返す."""
    sub = select_edge_attr(g, "type", t)
    pairs = [(n, tgt) for n in to_roots(g, t)]
    return _to_paths(sub, pairs)


def copy_old_edges(
    g: nx.DiGraph,
    old: Hashable,
    new: Hashable,
    *ignore: EdgeType,
) -> None:
    """oldエッジをnew nodeにコピー."""
    g.add_node(new)

    for pred, _, data in g.in_edges(old, data=True):
        if data["type"] in ignore:
            continue
        g.add_edge(pred, new, **data)
    for _, succ, data in g.out_edges(old, data=True):
        if data["type"] in ignore:
            continue
        g.add_edge(new, succ, **data)
