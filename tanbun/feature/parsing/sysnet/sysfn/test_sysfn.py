"""test."""

import networkx as nx
import pytest

from tanbun.feature.domain.graph import to_nested
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.term import Term, check_and_merge_term
from tanbun.feature.parsing.primitive.term.markresolver import MarkResolver
from tanbun.feature.parsing.sysnet.errors import (
    SentenceConflictError,
    SysNetNotFoundError,
)
from tanbun.feature.parsing.sysnet.sysfn import get_ifdef, to_sentence, to_term
from tanbun.feature.parsing.sysnet.sysfn.build_fn import add_resolved_edges
from tanbun.feature.parsing.sysnet.sysnode import Def

from . import check_duplicated_sentence


def test_duplicate_sentence() -> None:
    """文重複."""
    with pytest.raises(SentenceConflictError):
        check_duplicated_sentence(["aaa", "aaa"])


def test_reusable_sentence_is_not_a_duplicate() -> None:
    """共有ノードとして指定した回数分は重複を許可する."""
    check_duplicated_sentence(
        ["フランス", "フランス"],
        reusable=["フランス", "フランス"],
    )


def test_reusable_sentence_does_not_hide_other_duplicates() -> None:
    """共有参照を超えた通常文の重複は引き続き検出する."""
    with pytest.raises(SentenceConflictError):
        check_duplicated_sentence(
            ["フランス", "フランス", "フランス", "フランス"],
            reusable=["フランス", "フランス"],
        )


def test_resolved() -> None:
    """用語解決."""
    root = "sys"
    g = nx.MultiDiGraph()
    g.add_node(root)
    EdgeType.BELOW.add_path(g, root, "h1", "h2")
    defs = [
        Def.create("df", ["A"]),  # term, sentence 0 0
        Def.create("b{A}b", ["B"]),  # 0 1
        Def.create("ccc", ["C{B}"]),  # 1 0
        Def.create("d{CB}d", ["D"]),  # 2 0
    ]
    EdgeType.SIBLING.add_path(g, "h1", *to_sentence(defs))
    defs2 = [
        Def.create("ppp", ["P{D}"]),
        Def.create("qqq", ["Q"]),
        Def.dummy_from("X"),
    ]
    EdgeType.SIBLING.add_path(g, "h2", *to_sentence(defs2))
    [d.add_edge(g) for d in [*defs, *defs2]]
    mt = check_and_merge_term(to_term([*defs, *defs2]))
    resolver = MarkResolver.create(mt)
    add_resolved_edges(g, resolver)
    access = EdgeType.RESOLVED.succ
    assert to_nested(g, "df", access) == {}
    assert to_nested(g, "b{A}b", access) == {"df": {}}
    assert to_nested(g, "ccc", access) == {"b{A}b": {"df": {}}}
    assert to_nested(g, "d{CB}d", access) == {"ccc": {"b{A}b": {"df": {}}}}
    assert to_nested(g, "ppp", access) == {"d{CB}d": {"ccc": {"b{A}b": {"df": {}}}}}
    assert to_nested(g, "qqq", access) == {}


def test_get() -> None:
    """文に紐づく用語があれば定義を返す."""
    root = "sys"
    g = nx.MultiDiGraph()
    g.add_node(root)
    df = Def.create("aaa", ["A"])
    t = Def.dummy_from("B")
    EdgeType.BELOW.add_edge(g, root, df)
    EdgeType.BELOW.add_edge(g, root, "bbb")
    EdgeType.BELOW.add_edge(g, root, t)
    df.add_edge(g)
    t.add_edge(g)
    assert get_ifdef(g, "aaa") == df
    assert get_ifdef(g, Term.create("A")) == df
    assert get_ifdef(g, "bbb") == "bbb"
    assert get_ifdef(g, t.term) == t
    with pytest.raises(SysNetNotFoundError):
        get_ifdef(g, "dummy")
    with pytest.raises(SysNetNotFoundError):
        get_ifdef(g, Term.create("dummy"))


def test_duplicable() -> None:
    """重複可能な文."""
    root = "sys"
    g = nx.MultiDiGraph()
    g.add_node(root)
    d1 = Duplicable(n="+++ dup1 +++")
    d2 = Duplicable(n="+++ dup1 +++")
    EdgeType.BELOW.add_edge(g, root, d1)
    EdgeType.SIBLING.add_path(g, d1, d2)
    assert to_sentence(g.nodes) == [root, d1, d2]
