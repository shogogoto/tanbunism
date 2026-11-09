"""1network 1nodeを引数にする関数."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import TYPE_CHECKING, Final

from tanbun.feature.domain.graph import (
    select_edge_attr,
    to_nodes,
    to_roots,
)
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import KNArg, KNode

if TYPE_CHECKING:
    import networkx as nx

type Nw1N1Fn = Callable[[SysNet, KNode], list[KNode]]


class ParentLookupError(Exception):
    """親テーブルを作る時に変なことが起きた."""


def parent_lookup(g: nx.DiGraph) -> dict:
    """親の辞書."""
    g = select_edge_attr(g, "type", EdgeType.BELOW, EdgeType.SIBLING)
    parents = to_roots(g, EdgeType.BELOW)
    lookup = {}
    for p in parents:
        first_sibs = EdgeType.BELOW.succ(g, p)
        for s in first_sibs:
            lookup[s] = p
            for s_ in to_nodes(g, s, EdgeType.SIBLING.succ):
                lookup[s_] = p
    return lookup


class NwOp:
    """ネットワーク操作用名前空間."""

    @staticmethod
    def get_detail(g: nx.DiGraph, n: Hashable) -> list[KNode]:
        """詳細な記述."""
        vals = []
        # Accessors already filter edge types; do not copy the whole graph per node.
        for below_s in EdgeType.BELOW.succ(g, n):
            sibs = to_nodes(g, below_s, EdgeType.SIBLING.succ)
            vals.extend(sibs)
        return vals

    @staticmethod
    def get_parent_or_none(sn: SysNet, n: KNode) -> KNode | None:
        """兄弟を辿ったらBELOW.predが存在するか."""
        lookup = sn.parents
        return lookup.get(n)

    @staticmethod
    def get_refer(sn: SysNet, n: KNode) -> list[KNode]:
        """引用・利用する側."""
        return sn.access(n, EdgeType.RESOLVED.pred)

    @staticmethod
    def get_referred(sn: SysNet, n: KNode) -> list[KNode]:
        """引用される依存元."""
        return sn.access(n, EdgeType.RESOLVED.succ)

    @staticmethod
    def get_premise(sn: SysNet, n: KNode) -> list[KNode]:
        """前提."""
        return sn.access(n, EdgeType.TO.pred)

    @staticmethod
    def get_conclusion(sn: SysNet, n: KNode) -> list[KNode]:
        """帰結."""
        return sn.access(n, EdgeType.TO.succ)

    @staticmethod
    def get_example(sn: SysNet, n: KNode) -> list[KNode]:
        """具体."""
        return sn.access(n, EdgeType.EXAMPLE.succ)

    @staticmethod
    def get_general(sn: SysNet, n: KNode) -> list[KNode]:
        """抽象."""
        return sn.access(n, EdgeType.EXAMPLE.pred)

    @staticmethod
    def recursively_nw1n1(fn: Nw1N1Fn, count: int) -> Nw1N1Fn:
        """再帰的取得."""
        if count < 1:
            msg = "recursive count must be greater than 0"
            raise ValueError(msg)

        def _f(sn: SysNet, ns: Iterable, i: int) -> Iterable:
            """再帰."""
            if i == 0:
                return ns
            return [
                e if isinstance(e, list) else [e, _f(sn, fn(sn, e), i - 1)] for e in ns
            ]

        def _g(sn: SysNet, n: KNode) -> list:
            ret = fn(sn, n)
            return list(_f(sn, ret, count - 1))

        return _g


#######################################################

# 依存性関連エッジ
DEP_EDGE_TYPES: Final = [
    EdgeType.TO,
    EdgeType.EXAMPLE,
    EdgeType.SIMILAR,
    EdgeType.ANTI,
    EdgeType.RESOLVED,
]


def has_dependency(sn: SysNet, n: KNArg) -> bool:
    """意味的に重要なエッジを持つ."""
    in_ = sn.g.in_edges(n, data=True)
    for _u, _, attr in in_:
        if attr["type"] in DEP_EDGE_TYPES:
            return True

    out = sn.g.out_edges(n, data=True)
    return any(attr["type"] in DEP_EDGE_TYPES for _, _v, attr in out)
