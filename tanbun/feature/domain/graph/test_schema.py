"""グラフのPython・JSON出力とOpenAPI契約."""

import json
from typing import Any
from uuid import UUID

import networkx as nx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.graph.schema import NXGraph
from tanbun.feature.domain.types import Duplicable


class GraphEnvelope(BaseModel):
    """公開APIと同じく、モデル内にグラフを格納する."""

    g: NXGraph


class AttributeValue(BaseModel):
    """グラフ属性に格納する構造化データ."""

    name: str


@pytest.mark.parametrize("graph_class", [nx.DiGraph, nx.MultiDiGraph])
def test_graph_serialization_preserves_wire_data_without_warnings(graph_class) -> None:
    """入力ID・複数辺のキー・ノードや辺の追加属性を変更しない."""
    graph = graph_class()
    graph.graph.update(name="example", metadata={"nested": [1, 2]})
    graph.add_node(0, label="zero", color="red")
    graph.add_node("end", label="終点")
    graph.add_node("isolated", extra={"a": "b"})
    if graph.is_multigraph():
        graph.add_edge(0, "end", key="primary", type=EdgeType.TO, weight=2)
        graph.add_edge(0, "end", key=7, type="custom", extra=["value"])
    else:
        graph.add_edge(0, "end", type=EdgeType.TO, weight=2)
    data = nx.node_link_data(graph, edges="edges")
    expected = TypeAdapter(Any).dump_python(data, mode="json", warnings="error")
    model = GraphEnvelope(g=graph)
    assert model.model_dump(warnings="error") == {"g": data}
    assert model.model_dump(mode="json", warnings="error") == {"g": expected}
    assert json.loads(model.model_dump_json(warnings="error")) == {"g": expected}
    assert "weight" in expected["edges"][0]
    assert nx.node_link_data(graph, edges="edges") == data
    restored = GraphEnvelope.model_validate_json(
        model.model_dump_json(warnings="error"),
    )
    assert nx.node_link_data(restored.g, edges="edges") == expected
    if not graph.is_multigraph():
        assert "key" not in expected["edges"][0]


@pytest.mark.parametrize("graph_class", [nx.DiGraph, nx.MultiDiGraph])
def test_empty_graph_output_matches_networkx(graph_class) -> None:
    """点・辺がないグラフも、同じnode-link形式で警告なく返す."""
    graph = graph_class()
    assert GraphEnvelope(g=graph).model_dump(warnings="error") == {
        "g": nx.node_link_data(graph, edges="edges"),
    }


def test_non_string_ids_and_domain_objects_keep_existing_json_representation() -> None:
    """UUIDやモデル属性を通常のPydantic JSON出力と揃える."""
    graph = nx.MultiDiGraph()
    uid = UUID("12345678-1234-5678-1234-567812345678")
    value = AttributeValue(name="payload")
    duplicate = Duplicable(n="同じ文", uid=uid)
    graph.add_node(uid, payload=value)
    graph.add_node(duplicate, payload=duplicate)
    graph.add_edge(
        uid,
        duplicate,
        key=uid,
        type=EdgeType.ANTI,
        metadata={"value": value},
    )
    expected = TypeAdapter(Any).dump_python(
        nx.node_link_data(graph, edges="edges"),
        mode="json",
        warnings="error",
    )
    assert (
        TypeAdapter(NXGraph).dump_python(graph, mode="json", warnings="error")
        == expected
    )
    assert json.loads(GraphEnvelope(g=graph).model_dump_json(warnings="error")) == {
        "g": expected,
    }


def test_fastapi_response_and_openapi_describe_the_actual_output() -> None:
    """出力のJSON形式と公開schemaの必須キー・追加属性の扱いを一致させる."""
    app = FastAPI()
    graph = nx.DiGraph()
    graph.add_edge(1, "end", label="edge")

    @app.get("/graph")
    def read_graph() -> GraphEnvelope:
        return GraphEnvelope(g=graph)

    with TestClient(app) as client:
        response = client.get("/graph")
    assert response.status_code == 200  # noqa: PLR2004
    assert response.json()["g"] == nx.node_link_data(graph, edges="edges")
    schemas = app.openapi()["components"]["schemas"]
    output = schemas["_GraphData"]
    assert set(output["required"]) == {
        "directed",
        "multigraph",
        "graph",
        "nodes",
        "edges",
    }
    edge = schemas["_EdgeData"]
    assert set(edge["required"]) == {"source", "target"}
    assert edge["additionalProperties"] is True
    assert "key" in edge["properties"]
    node = schemas["_NodeData"]
    assert node["required"] == ["id"]
    assert node["additionalProperties"] is True
