"""edge type test."""

import networkx as nx
from pytest_unordered import unordered

from tanbun.feature.domain.graph.edge_type import EdgeType, etype_subgraph


def test_subgraphs_observe_new_and_removed_nodes() -> None:
    """同じグラフの構造変更を型別サブグラフでも反映する."""
    g = nx.MultiDiGraph()
    EdgeType.BELOW.add_edge(g, "a", "b")
    assert set(EdgeType.BELOW.subgraph(g)) == {"a", "b"}
    assert set(etype_subgraph(g, EdgeType.BELOW)) == {"a", "b"}
    EdgeType.BELOW.add_edge(g, "b", "c")
    assert set(EdgeType.BELOW.subgraph(g)) == {"a", "b", "c"}
    assert set(etype_subgraph(g, EdgeType.BELOW)) == {"a", "b", "c"}
    g.remove_node("a")
    assert set(EdgeType.BELOW.subgraph(g)) == {"b", "c"}
    assert set(etype_subgraph(g, EdgeType.BELOW)) == {"b", "c"}


def test_subgraph() -> None:
    """サブグラフの取得."""
    g = nx.MultiDiGraph()

    t = EdgeType.TO
    t.add_edge(g, "a", "a0")
    t.add_edge(g, "a", "a1")
    EdgeType.ANTI.add_edge(g, "a", "x")
    t.add_edge(g, "a1", "a11")
    sub = t.subgraph(g)
    assert list(sub.nodes) == unordered(["a", "a0", "a1", "a11"])


def test_path2edgetypes2():
    """Graph pathからedgetypeのリストを得る."""
    """
    # title
        aaa
        bbb
        parent
            ccc
                ccc1
                ccc2
                ccc3
                -> to
                    todetail
                    -> ccc5
                <- cccb
                    <- cccb1
    """

    g = nx.MultiDiGraph()
    g.add_node("# title")
    EdgeType.BELOW.add_edge(g, "# title", "aaa")
    EdgeType.SIBLING.add_path(g, "aaa", "bbb", "parent")
    EdgeType.BELOW.add_path(g, "parent", "ccc", "ccc1")
    EdgeType.SIBLING.add_path(g, "ccc1", "ccc2", "ccc3")
    EdgeType.TO.add_path(g, "ccc", "to", "ccc5")
    EdgeType.SIBLING.add_path(g, "to", "todetail")
    EdgeType.TO.add_path(g, "cccb1", "cccb", "ccc")

    assert EdgeType.path2edgetypes(g, "ccc", "ccc1") == ([EdgeType.BELOW], True)
    assert EdgeType.path2edgetypes(g, "ccc1", "ccc") == ([EdgeType.BELOW], False)
    assert EdgeType.path2edgetypes(g, "ccc", "parent") == ([EdgeType.BELOW], False)
    assert EdgeType.path2edgetypes(g, "parent", "ccc") == ([EdgeType.BELOW], True)
    assert EdgeType.path2edgetypes(g, "ccc", "ccc3") == (
        [EdgeType.BELOW, *[EdgeType.SIBLING] * 2],
        True,
    )
    assert EdgeType.path2edgetypes(g, "ccc3", "ccc") == (
        [EdgeType.BELOW, *[EdgeType.SIBLING] * 2],
        False,
    )
    assert EdgeType.path2edgetypes(g, "ccc", "ccc5") == ([EdgeType.TO] * 2, True)
    assert EdgeType.path2edgetypes(g, "ccc5", "ccc") == ([EdgeType.TO] * 2, False)
    assert EdgeType.path2edgetypes(g, "ccc", "cccb1") == ([EdgeType.TO] * 2, False)
    assert EdgeType.path2edgetypes(g, "cccb1", "ccc") == ([EdgeType.TO] * 2, True)
