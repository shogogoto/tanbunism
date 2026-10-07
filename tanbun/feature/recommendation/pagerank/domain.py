"""PageRankの運用設定・計算規則."""

import networkx as nx
from pydantic import BaseModel, Field

VERSION = 1
LEASE_SECONDS = 120


class PageRankSettings(BaseModel, frozen=True):
    """他の重い処理と独立した上限."""

    max_concurrent_jobs: int = Field(default=1, ge=1, le=4, strict=True)
    max_pending_jobs: int = Field(default=10, ge=1, le=100, strict=True)
    max_nodes: int = Field(default=20000, ge=1, le=100000, strict=True)
    max_edges: int = Field(default=100000, ge=1, le=500000, strict=True)


class PageRankRequest(BaseModel):
    """空の選択・重複を受け付けない一括要求."""

    resource_ids: list[str] = Field(min_length=1, max_length=2000)


def calculate_ranks(
    nodes: list[str],
    edges: list[tuple[str, str, str]],
) -> dict[str, float]:
    """参照先・推論の前提に票を集める。重複・自己辺・階層は除外."""
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    for source, target, kind in edges:
        if source == target or source not in graph or target not in graph:
            continue
        if kind == "TO":
            graph.add_edge(target, source)
        elif kind in {"RESOLVED", "REF"}:
            graph.add_edge(source, target)
    if not nodes:
        return {}
    return nx.pagerank(graph, alpha=0.85, max_iter=100, tol=1e-8)
