"""networkx関連テスト."""

import gc
import weakref

import networkx as nx
import pytest

from tanbun.feature.domain.graph.errors import GraphCycleError
from tanbun.feature.domain.graph.util import succ_attr

from . import (
    leaf_paths,
    root_paths,
    select_edge_attr,
    to_leaves,
    to_nested,
    to_nodes,
    to_roots,
)


def test_to_nodes_returns_only_unique_nodes_in_depth_first_order() -> None:
    """合流・循環でも初出順を保ち、Noneや重複を混入させない."""
    g = nx.DiGraph([
        (0, "left"),
        (0, "right"),
        ("left", "end"),
        ("right", "end"),
        ("end", 0),
    ])
    assert to_nodes(g, 0, nx.DiGraph.successors) == [0, "left", "end", "right"]
    assert to_nodes(g, "end", nx.DiGraph.predecessors) == ["end", "left", 0, "right"]


def test_to_nodes_isolated_node_and_self_loop() -> None:
    """孤立点・自己参照は開始ノード一つだけを返す."""
    g = nx.DiGraph()
    g.add_node("alone")
    assert to_nodes(g, "alone", nx.DiGraph.successors) == ["alone"]
    g.add_edge("alone", "alone")
    assert to_nodes(g, "alone", nx.DiGraph.successors) == ["alone"]


def test_deep_traversals_do_not_use_the_python_call_stack() -> None:
    """長い兄弟列や引用連鎖も再帰上限に依存しない."""
    depth = 3000
    g = nx.path_graph(depth, create_using=nx.DiGraph)
    assert to_nodes(g, 0, nx.DiGraph.successors) == list(range(depth))
    nested = to_nested(g, 0, nx.DiGraph.successors)
    for node in range(1, depth):
        assert list(nested) == [node]
        nested = nested[node]
    assert nested == {}


@pytest.mark.parametrize("edges", [[("a", "a")], [("a", "b"), ("b", "a")]])
def test_to_nested_rejects_cycles_explicitly(edges: list[tuple[str, str]]) -> None:
    """循環を葉に偽装したり再帰エラーにせず、変換不可を明示する."""
    g = nx.DiGraph(edges)
    with pytest.raises(GraphCycleError, match="cyclic graph"):
        to_nested(g, "a", nx.DiGraph.successors)


def test_to_nested_keeps_both_converging_branches_independent() -> None:
    """全体の訪問済み集合で合流先の枝を落とさない."""
    g = nx.DiGraph([("root", "a"), ("root", "b"), ("a", "c"), ("b", "c")])
    result = to_nested(g, "root", nx.DiGraph.successors)
    assert result == {"a": {"c": {}}, "b": {"c": {}}}
    result["a"]["c"]["local"] = {}
    assert result["b"] == {"c": {}}
    assert to_nested(g, "root", nx.DiGraph.successors) == {
        "a": {"c": {}},
        "b": {"c": {}},
    }


def test_queries_observe_graph_changes_and_return_fresh_results() -> None:
    """同一インスタンスの辺追加・削除・属性変更を直ちに反映する."""
    g = nx.DiGraph()
    g.add_edge("a", "b", type="x")
    access = nx.DiGraph.successors
    assert to_nodes(g, "a", access) == ["a", "b"]
    assert to_nested(g, "a", access) == {"b": {}}
    assert to_leaves(g, "x") == ["b"]
    assert to_roots(g, "x") == ["a"]
    selected = select_edge_attr(g, "type", "x")
    selected.add_edge("local", "only", type="x")
    g.add_edge("b", "c", type="x")
    assert to_nodes(g, "a", access) == ["a", "b", "c"]
    assert to_nested(g, "a", access) == {"b": {"c": {}}}
    assert list(select_edge_attr(g, "type", "x").edges) == [("a", "b"), ("b", "c")]
    assert to_leaves(g, "x") == ["c"]
    g["a"]["b"]["type"] = "y"
    assert to_roots(g, "x") == ["b"]
    g.remove_edge("b", "c")
    assert to_leaves(g, "x") == []
    assert list(select_edge_attr(g, "type", "x").edges) == []


def test_graph_helpers_do_not_keep_input_graphs_alive() -> None:
    """一時的に解析したグラフをプロセス全体のキャッシュへ残さない."""
    g = nx.DiGraph()
    g.add_edge("a", "b", type="x")
    reference = weakref.ref(g)
    to_nodes(g, "a", nx.DiGraph.successors)
    to_nested(g, "a", nx.DiGraph.successors)
    select_edge_attr(g, "type", "x")
    to_leaves(g, "x")
    to_roots(g, "x")
    del g
    gc.collect()
    assert reference() is None


def test_to_nested() -> None:  # noqa: D103
    g = nx.MultiDiGraph()
    g.add_edge("sys", "h1", type="x")
    g.add_edge("h1", "h12", type="x")
    g.add_edge("h12", "h121", type="x")
    g.add_edge("h12", "h122", type="x")
    g.add_edge("sys", "h2", type="x")
    g.add_edge("sys", "dummy")
    assert to_nested(g, "sys", succ_attr("type", "x")) == {
        "h1": {
            "h12": {"h121": {}, "h122": {}},
        },
        "h2": {},
    }


def test_filter_edge_attr() -> None:
    """特定の値を持ったedge attrで."""
    g = nx.MultiDiGraph()
    nx.add_path(g, ["sys", *[f"a{i}" for i in range(2)]], type="A")
    nx.add_path(g, ["sys", *[f"b{i}" for i in range(2)]], type="B")
    sub = select_edge_attr(g, "type", "A")
    assert list(nx.shortest_path(g, "sys", "b1")) == ["sys", "b0", "b1"]
    with pytest.raises(nx.NodeNotFound):
        nx.shortest_path(sub, "sys", "b1")


def test_leaf_paths() -> None:
    """終点となる要素までのpathsを取得."""
    # ╙── 0
    #     ├─╼ 1
    #     │   ├─╼ 4
    #     │   ├─╼ 5
    #     │   └─╼ 6
    #     ├─╼ 2
    #     │   ├─╼ 7
    #     │   ├─╼ 8
    #     │   └─╼ 9 ╼ dummy
    #     └─╼ 3
    #         ├─╼ 10
    #         ├─╼ 11
    #         └─╼ 12
    g = nx.balanced_tree(3, 2, nx.MultiDiGraph())
    nx.set_edge_attributes(g, "x", "type")
    g.add_edge(9, "dummy", type="dummy")
    assert leaf_paths(g, 7, "x") == [[7]]
    assert leaf_paths(g, 2, "x") == [[2, 7], [2, 8], [2, 9]]
    assert leaf_paths(g, 2, "y") == []


def test_axiom_paths() -> None:
    """出発点となる要素までのpathsを取得."""
    g = nx.balanced_tree(3, 2, nx.MultiDiGraph())
    nx.set_edge_attributes(g, "x", "type")
    g.add_edge(9, "dummy", type="dummy")
    assert root_paths(g, 7, "x") == [[0, 2, 7]]
    assert root_paths(g, 2, "x") == [[0, 2]]
    with pytest.raises(nx.NodeNotFound):
        root_paths(g, "dummy", "x")


def test_axioms() -> None:
    """出発点となるnodesを取得."""
    g = nx.balanced_tree(3, 2, nx.MultiDiGraph()).reverse()  # 逆向き
    nx.set_edge_attributes(g, "x", "type")
    assert to_roots(g, "x") == [4, 5, 6, 7, 8, 9, 10, 11, 12]
