"""知識グラフのAPI schema."""

from typing import Annotated, Any, NotRequired, TypedDict, cast

import networkx as nx
from pydantic import (
    ConfigDict,
    PlainSerializer,
    PlainValidator,
    SerializeAsAny,
    ValidationInfo,
    with_config,
)

from .edge_type import EdgeType


def _validate_graph(v: Any, info: ValidationInfo) -> nx.DiGraph:
    if isinstance(v, dict):
        return nx.node_link_graph(v, edges="edges")
    if isinstance(v, nx.DiGraph):
        return v
    raise TypeError


@with_config(ConfigDict(extra="allow"))
class _EdgeData(TypedDict):
    """任意属性を持つnode-link形式の辺。keyは複数辺の場合だけ存在する."""

    source: Any
    target: Any
    type: NotRequired[EdgeType | str]
    key: NotRequired[Any]


@with_config(ConfigDict(extra="allow"))
class _NodeData(TypedDict):
    """文字列以外の識別子や任意属性も保持するnode-link形式の点."""

    id: Any


class _GraphData(TypedDict):
    """実際に返す辞書形式と一致する、グラフの出力schema."""

    directed: bool
    multigraph: bool
    graph: dict[Any, Any]
    nodes: list[SerializeAsAny[_NodeData]]
    edges: list[SerializeAsAny[_EdgeData]]


def _serialize_graph(graph: nx.DiGraph) -> _GraphData:
    """NetworkXのnode-link形式を、属性やキーを削らずそのまま返す."""
    return cast("_GraphData", nx.node_link_data(graph, edges="edges"))


NXGraph = Annotated[
    nx.DiGraph,
    PlainValidator(_validate_graph, json_schema_input_type=dict),
    PlainSerializer(_serialize_graph, return_type=_GraphData),
]
