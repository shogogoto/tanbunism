"""netwok1 node1 getter."""

import gc
import weakref

import networkx as nx
from pytest_unordered import unordered

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.parsing.sysnet.systats.nw1_n1 import (
    NwOp,
    has_dependency,
    parent_lookup,
)
from tanbun.feature.parsing.sysnet.systats.nw1_n1.ctxdetail import to_nw1n1fn
from tanbun.feature.parsing.tree2net import parse2net, parse2net_uncached


def test_parent_lookup_observes_graph_mutations_and_is_not_shared() -> None:
    """可変グラフの親取得で古い結果や呼び出し側の変更を再利用しない."""
    g = nx.DiGraph()
    EdgeType.BELOW.add_edge(g, "p", "a")
    lookup = parent_lookup(g)
    assert lookup == {"a": "p"}
    lookup["a"] = "local"
    EdgeType.SIBLING.add_edge(g, "a", "b")
    assert parent_lookup(g) == {"a": "p", "b": "p"}


def test_parents_are_reused_only_within_the_parsed_snapshot() -> None:
    """ノードごとの親取得で再計算せず、別リソースへは持ち越さない."""
    first = parse2net_uncached("# title\n  parent\n    a\n    b\n")
    second = parse2net_uncached("# other\n  another\n    a\n")
    assert first.parents is first.parents
    assert NwOp.get_parent_or_none(first, "b") == "parent"
    assert NwOp.get_parent_or_none(second, "a") == "another"
    assert first.parents is not second.parents


def test_parsed_snapshot_is_not_retained_by_parent_and_dependency_helpers() -> None:
    """親テーブルの再利用が解析結果をプロセス全体へ保持しない."""
    snapshot = parse2net_uncached("# title\n  parent\n    a\n")
    reference = weakref.ref(snapshot.g)
    assert NwOp.get_parent_or_none(snapshot, "a") == "parent"
    assert not has_dependency(snapshot, "a")
    del snapshot
    gc.collect()
    assert reference() is None


def test_get_detail_parent() -> None:
    """詳細."""
    s = r"""
        # h1
            aaa
            B: bbb
                b1
                b2
                    b21
                    b22
                    b23
        ## h2
            ccc
                c1
                c2
            `B`
                1b
                2b
    """
    sn = parse2net(s)
    assert NwOp.get_detail(sn.g, "aaa") == []
    assert sn.access("bbb", NwOp.get_detail) == unordered(["b1", "b2", "1b", "2b"])

    assert NwOp.get_parent_or_none(sn, "aaa") is None
    assert NwOp.get_parent_or_none(sn, "b2") == "bbb"
    assert NwOp.get_parent_or_none(sn, "b21") == "b2"
    assert NwOp.get_parent_or_none(sn, "b22") == "b2"
    assert NwOp.get_parent_or_none(sn, "b23") == "b2"
    assert NwOp.get_parent_or_none(sn, "# h1") is None
    assert NwOp.get_parent_or_none(sn, "## h2") is None


def test_has_dependency() -> None:
    """意味的な依存を持つ."""
    s = r"""
        # h1
        ## h2
        ### h3
            aaa
                -> bbb
                <- 000
                <-> ccc
                e.g. ddd
                when. 1919
                where. japan
                ~= eee
                g.e. fff
                by. x-men
            bro
                little
    """
    sn = parse2net(s)
    assert has_dependency(sn, "aaa")
    assert not has_dependency(sn, "bro")


def test_refer_referred() -> None:
    """用語引用."""
    s = r"""
        # h1
            A: aaa
            B: b{A}b
            C: c{A}c
            D: d{C}d
            E{D}: eee
    """
    sn = parse2net(s)
    assert NwOp.get_refer(sn, "aaa") == unordered(["b{A}b", "c{A}c"])
    assert NwOp.get_refer(sn, "b{A}b") == []
    assert NwOp.get_refer(sn, "c{A}c") == ["d{C}d"]
    assert NwOp.get_refer(sn, "d{C}d") == ["eee"]
    assert NwOp.get_refer(sn, "eee") == []

    assert NwOp.get_referred(sn, "aaa") == []
    assert NwOp.get_referred(sn, "b{A}b") == ["aaa"]
    assert NwOp.get_referred(sn, "c{A}c") == ["aaa"]
    assert NwOp.get_referred(sn, "d{C}d") == ["c{A}c"]
    assert NwOp.get_referred(sn, "eee") == ["d{C}d"]


def test_premise_conclusion() -> None:
    """用語引用."""
    s = r"""
        # h1
            aaa
                -> A| bbb
                <- 000
                <-> ccc
            bro
                little
            `A`
                -> ddd

    """
    sn = parse2net(s)
    assert NwOp.get_premise(sn, "bbb") == ["aaa"]
    assert NwOp.get_conclusion(sn, "bbb") == ["ddd"]


def test_example_general() -> None:
    """具体抽象."""
    s = r"""
        # h1
            aaa
                ex. a1
                ex. a2
                    ex. a21
                    ex. a22
                    ex. a23
    """
    sn = parse2net(s)
    assert NwOp.get_example(sn, "aaa") == ["a1", "a2"]
    assert NwOp.get_general(sn, "a22") == ["a2"]


def test_recursively() -> None:
    """再帰的取得."""
    s = r"""
        # h1
            aaa
                -> bbb
                -> ccc
                    -> ddd
                        -> eee
                    -> fff
                -> ggg
    """
    sn = parse2net(s)
    f1 = NwOp.recursively_nw1n1(NwOp.get_conclusion, 1)
    assert f1(sn, "aaa") == ["bbb", "ccc", "ggg"]
    f2 = NwOp.recursively_nw1n1(NwOp.get_conclusion, 2)
    assert f2(sn, "aaa") == [["bbb", []], ["ccc", ["ddd", "fff"]], ["ggg", []]]
    f3 = NwOp.recursively_nw1n1(NwOp.get_conclusion, 3)
    assert f3(sn, "aaa") == [
        ["bbb", []],
        ["ccc", [["ddd", ["eee"]], ["fff", []]]],
        ["ggg", []],
    ]
    for i in range(4, 7):
        f4 = NwOp.recursively_nw1n1(NwOp.get_conclusion, i)
        assert f4(sn, "aaa") == [
            ["bbb", []],
            ["ccc", [["ddd", [["eee", []]]], ["fff", []]]],
            ["ggg", []],
        ]


def test_get_detail_recursively() -> None:
    """詳細の再帰的取得."""
    s = r"""
        # h
            1
                11
                    21
                        31
                        32
                    22
                        33
                        34
                            41
                            42
                12

    """
    sn = parse2net(s)
    detail_fn = to_nw1n1fn(NwOp.get_detail)
    f1 = NwOp.recursively_nw1n1(detail_fn, 1)
    assert f1(sn, "1") == ["11", "12"]
    f2 = NwOp.recursively_nw1n1(detail_fn, 3)
    assert f2(sn, "1") == [
        ["11", [["21", ["31", "32"]], ["22", ["33", "34"]]]],
        ["12", []],
    ]


def test_get_refer_recursively() -> None:
    """referの再帰的取得."""
    s = r"""
        # h
            A: aaa
            B: b{A}
            C: c{B}
            D: d{C}
            E: e{D}
            F: f{E}
            G: g{F}
    """
    sn = parse2net(s)
    f1 = NwOp.recursively_nw1n1(NwOp.get_refer, 1)
    assert f1(sn, "aaa") == ["b{A}"]
    f2 = NwOp.recursively_nw1n1(NwOp.get_refer, 3)
    assert f2(sn, "aaa") == [["b{A}", [["c{B}", ["d{C}"]]]]]
    f3 = NwOp.recursively_nw1n1(NwOp.get_refer, 5)
    assert f3(sn, "aaa") == [["b{A}", [["c{B}", [["d{C}", [["e{D}", ["f{E}"]]]]]]]]]
