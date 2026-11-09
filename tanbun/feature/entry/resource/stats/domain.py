"""domain."""

import operator
from collections.abc import Hashable
from functools import reduce
from typing import Self

import networkx as nx
import pydantic_partial
from pydantic import BaseModel, Field, computed_field

from tanbun.feature.domain.graph.edge_type import (
    EdgeType,
    etype_subgraph,
    is_leaf,
    is_root,
)
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import KNArg
from tanbun.feature.parsing.sysnet.systats.nw1_n1 import (
    NwOp,
    has_dependency,
)


class ResourceStatsBasic(BaseModel):
    """基本的な計数値."""

    n_char: int = Field(title="文字数", description="テキストの絶対的なボリューム")
    n_sentence: int = Field(title="単文数", description="知識の基本的な構成単位の数")
    n_term: int = Field(title="単語数", description="語彙の規模")
    n_edge: int = Field(title="辺数", description="知識間の関係性の数")
    n_isolation: int = Field(title="孤立単文数")
    n_axiom: int = Field(title="公理数")
    n_unrefered: int = Field(
        title="未参照用語数",
        description="他のどこからも参照されていない用語数",
    )

    @classmethod
    def create(cls, sn: SysNet) -> Self:
        """Create stats."""

        def n_char(sn: SysNet) -> int:
            c = 0
            for n in sn.g.nodes:
                match n:
                    case str() | Duplicable():
                        c += len(str(n))
                    case Term():
                        c += (
                            0
                            if len(n.names) == 0
                            else reduce(operator.add, map(len, n.names))
                        )
                        c += len(n.alias) if n.alias else 0
                    case _:
                        raise TypeError
            return c

        return cls(
            n_char=n_char(sn),
            n_sentence=len(sn.sentences),
            n_term=len(sn.terms),
            n_edge=len(sn.g.edges),
            n_isolation=len(get_isolation(sn)),
            n_axiom=len(get_axiom(sn)),
            n_unrefered=len(get_unrefered(sn)),
        )


class ResourceStatsCohesion(ResourceStatsBasic):
    """まとまりの良さを示す指標."""

    @computed_field(
        title="孤立単文の割合",
        description="低いほど、知識が相互に接続されている",
    )
    @property
    def r_isolation(self) -> float:
        """孤立割合. 低いほどネットワークのまとまりが良い."""
        if self.n_sentence == 0:
            return 0.0
        return self.n_isolation / self.n_sentence

    @computed_field(
        title="公理割合",
        description="低いほど、少数の原理から多くの知識が得られている",
    )
    @property
    def r_axiom(self) -> float:
        """全単文に対する公理割合. 低いほどまとまりが良い. オッカムの剃刀的な."""
        if self.n_sentence == 0:
            return 0.0
        return self.n_axiom / self.n_sentence

    @computed_field(
        title="未参照用語割合",
        description="低いほど、定義された用語が無駄なく活用されている",
    )
    @property
    def r_unrefered(self) -> float:
        """全用語の未参照語割合. 少ないほどまとまりが良い."""
        if self.n_term == 0:
            return 0.0
        return self.n_unrefered / self.n_term


class ResourceStatsRichness(BaseModel):
    """豊かさを示す指標."""

    average_degree: float = Field(
        title="平均次数",
        description="一つの知識が平均していくつの他の知識と関連付いているか。高いほど、知識が密に関連し合う",
    )

    @classmethod
    def create(cls, sn: SysNet) -> Self:
        """Create stats."""
        g = sn.g
        avg_degree = 0.0 if g.order() == 0 else g.size() / g.order()
        return cls(average_degree=avg_degree)


class ResourceStatsHeavy(BaseModel):
    """グラフ理論の指標 計算重い."""

    density: float = Field(
        title="密度",
        description="辺の割合。高いほど、ノード同士が密に結合している",
    )
    diameter: float = Field(
        title="直径",
        description="最大離心距離。ネットワーク内の最も遠いノード間の距離。低いほど、ネットワークがコンパクトで情報の伝達効率が高い。非連結のグラフの場合は、最大の強連結成分に対して計算",
    )
    radius: float = Field(
        title="半径",
        description="各ノードからの最大距離の最小値。低いほど、中心的なノードから全体にアクセスしやすい。非連結のグラフの場合は、最大の強連結成分に対して計算",
    )
    n_scc: int = Field(
        title="強連結成分の数 Strongly Connected Components",
        description="グラフがいくつの独立した「島」に分かれているか。低いほど、知識が分断されていない",
    )

    @classmethod
    def create(cls, sn: SysNet) -> Self:
        """Create stats."""
        g = sn.g
        if g.order() == 0:
            return cls(density=0.0, diameter=0.0, radius=0.0, n_scc=0)

        n_scc = nx.number_strongly_connected_components(g)
        density = nx.density(g)

        scc_nodes_list = list(nx.strongly_connected_components(g))
        if not scc_nodes_list:
            return cls(density=density, diameter=0.0, radius=0.0, n_scc=n_scc)

        largest_scc_nodes = max(scc_nodes_list, key=len)
        largest_scc = g.subgraph(largest_scc_nodes)

        if largest_scc.order() <= 1:
            diameter = 0.0
            radius = 0.0
        else:
            diameter = nx.diameter(largest_scc)
            radius = nx.radius(largest_scc)

        return cls(
            density=density,
            diameter=diameter,
            radius=radius,
            n_scc=n_scc,
        )


class ResourceStats(
    ResourceStatsCohesion,
    ResourceStatsRichness,
    pydantic_partial.create_partial_model(ResourceStatsHeavy),
):
    """知識の量を示す指標 for API."""


def get_isolation(sn: SysNet) -> list[Hashable]:
    """孤立したノード.

    SIBLINGは無視 <=> parentを持たない
    HEAD、TO、RESOLVEDも無視
    """

    def include_isolation_node(n: Hashable) -> bool:
        """parentもなく、詳細もなく、TOなどの関係もない."""
        parent = NwOp.get_parent_or_none(sn, n)
        if parent is not None:
            return False
        if has_dependency(sn, n):
            return False
        detail = NwOp.get_detail(sn.g, n)
        return len(detail) == 0

    # まず detail方向で絞る
    sub = etype_subgraph(sn.sentence_graph, EdgeType.SIBLING, EdgeType.BELOW)
    sub = nx.subgraph_view(
        sub,
        filter_node=include_isolation_node,
    )
    return list(sub.nodes)


def get_axiom(sn: SysNet) -> list[Hashable]:
    """TOの出発点となるDef or sentence."""
    sub = nx.subgraph_view(
        etype_subgraph(sn.sentence_graph, EdgeType.TO),
        filter_node=lambda n: is_root(sn.g, n, EdgeType.TO),
    )
    return list(sub.nodes)


def get_unrefered(sn: SysNet) -> list[KNArg]:
    """RESOLVEDの出発点.

    公理に対応する出発点となる用語を無定義用語 undefined term
    これになぞらえた命名
    """
    sub = nx.subgraph_view(
        etype_subgraph(sn.sentence_graph, EdgeType.RESOLVED),
        filter_node=lambda n: is_leaf(sn.g, n, EdgeType.RESOLVED),  # 見出し削除
    )
    return list(sub.nodes)


def create_resource_stats(sn: SysNet) -> dict:
    """リソース統計値の作成."""
    retval = ResourceStatsCohesion.create(sn).model_dump()
    retval.update(ResourceStatsRichness.create(sn).model_dump())
    retval.update(ResourceStatsHeavy.create(sn).model_dump())
    return retval
