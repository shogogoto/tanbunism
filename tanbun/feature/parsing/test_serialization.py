"""知識の解析結果と基盤グラフのシリアライズの結合テスト."""

from typing import Any

import networkx as nx
from pydantic import TypeAdapter

from tanbun.feature.parsing.tree2net import parse2net_uncached


def test_parsed_knowledge_graph_serializes_without_losing_domain_attributes() -> None:
    """実際の用語・反対関係を含む解析結果も、警告なくそのまま出力する."""
    network = parse2net_uncached(
        "# graph\n  A: first\n    <-> B: second\n  {A} is useful\n",
    )
    expected = TypeAdapter(Any).dump_python(
        nx.node_link_data(network.g, edges="edges"),
        mode="json",
        warnings="error",
    )
    assert network.model_dump(mode="json", warnings="error")["g"] == expected
    assert any(edge.get("type") == "anti" for edge in expected["edges"])
