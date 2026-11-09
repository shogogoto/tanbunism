"""test."""

import networkx as nx
from lark import Token

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.parsing.primitive.heading import get_heading_path, get_headings

hpath = [Token(value=f"h{i}", type=f"H{i + 1}") for i in range(1, 4)]


def test_heading_queries_observe_graph_updates() -> None:
    """見出しの追加・接続変更と、返却集合の書き換えを混同しない."""
    root = Token(value="root", type="H1")
    first = Token(value="first", type="H2")
    second = Token(value="second", type="H2")
    g = nx.DiGraph()
    nx.add_path(g, [root, first, "sentence"])
    headings = get_headings(g)
    headings.clear()
    assert get_headings(g) == {root, first}
    assert get_heading_path(g, root, "sentence") == [root, first]
    nx.add_path(g, [root, second, "sentence"])
    g.remove_edge(first, "sentence")
    assert get_headings(g) == {root, first, second}
    assert get_heading_path(g, root, "sentence") == [root, second]


def test_get_headings() -> None:
    """見出し一覧."""
    root = Token(value="root", type="H1")
    g = nx.MultiDiGraph()
    EdgeType.BELOW.add_path(g, root, *hpath)
    assert get_headings(g) == {root, *hpath}


def test_heading_path() -> None:
    """任意のnodeから直近の見出しpathを取得."""
    r = Token(value="root", type="H1")
    g = nx.MultiDiGraph()
    g.add_node(r)
    EdgeType.BELOW.add_path(g, r, *hpath)
    EdgeType.SIBLING.add_path(g, "h1", "aaa")
    EdgeType.BELOW.add_path(g, "aaa", "Aaa", "AAa")
    EdgeType.SIBLING.add_path(g, "h2", "bbb", "ccc")
    EdgeType.SIBLING.add_path(g, r, "x")

    # 隣接
    assert get_heading_path(g, r, "x") == [r]  # root直下
    assert get_heading_path(g, r, "aaa") == [r, "h1"]  # 見出しの兄弟
    # 非隣接
    assert get_heading_path(g, r, "Aaa") == [r, "h1"]  # 文の下
    assert get_heading_path(g, r, "AAa") == [r, "h1"]  # 文の下の下
    assert get_heading_path(g, r, "bbb") == [r, "h1", "h2"]
    assert get_heading_path(g, r, "ccc") == [r, "h1", "h2"]
