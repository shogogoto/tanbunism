"""adjacent 直近の周辺情報.

    as logic
      premises -> self
      conclusions <- self
    as term
      refer この文が引用する文 参照 -> self
      referred この文が引用している文 被参照 <- self
    as part
      detail
      parent.

DB 検索
term: sentでマッチ
その文脈を加える
"""

import itertools
from collections.abc import Hashable, Iterable
from uuid import UUID

import networkx as nx
from networkx import DiGraph
from pydantic import BaseModel, Field, RootModel

from tanbun.feature.domain.graph import to_nodes
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.graph.schema import NXGraph
from tanbun.feature.entry.domain import ResourceInfo
from tanbun.feature.entry.mapper import MResource
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.sysnet.sysnode import Def
from tanbun.feature.user.public_schema import UserReadPublic


class Additional(BaseModel, frozen=True):
    """単文の付加情報."""

    when: str | None = None
    where: str | None = None
    by: str | None = None


class TanbunStats(BaseModel, frozen=True):
    """知識の関係統計."""

    # frontendのモック自動生成で数値の範囲を制限したい
    n_detail: int = Field(ge=-100, le=1000)
    n_premise: int = Field(ge=-100, le=1000)
    n_conclusion: int = Field(ge=-100, le=1000)
    n_refer: int = Field(ge=-100, le=1000)
    n_referred: int = Field(ge=-100, le=1000)
    # dist_axiom: int = Field(ge=-100, le=1000)
    # dist_leaf: int = Field(ge=-100, le=1000)
    score: int | None = Field(default=None, ge=-100, le=1000)

    def __str__(self) -> str:  # noqa: D105
        ls = [
            self.n_detail,
            self.n_premise,
            self.n_conclusion,
            self.n_refer,
            self.n_referred,
            # self.dist_axiom,
            # self.dist_leaf,
            self.score,
        ]
        return str(ls)


class Tanbun(BaseModel, frozen=True):
    """知識の最小単位."""

    sentence: str
    uid: UUID
    term: Term | None = None
    additional: Additional | None = None
    stats: TanbunStats
    resource_uid: UUID

    def __str__(self) -> str:  # noqa: D105
        a = self.additional
        t = f"[{self.term}]" if self.term else ""
        when = f"T({a.when})" if a is not None and a.when else ""
        # stats = f"S({self.stats})" if self.stats else ""
        return f"{self.sentence}{t}{when}"

    def __repr__(self) -> str:  # noqa: D105
        return str(self)

    def when(self) -> str:  # noqa: D102
        a = self.additional
        return f"T({a.when})" if a is not None and a.when else ""

    @property
    def sentence_or_def(self) -> str | Def:  # noqa: D102
        if self.term is None:
            return self.sentence
        return Def(term=self.term, sentence=self.sentence)


class TanbunSearchResult(BaseModel):
    """単文検索結果."""

    total: int
    data: list[Tanbun]
    resource_infos: dict[UUID, ResourceInfo]


class UidStr(BaseModel):
    """UUID付き文章."""

    val: str
    uid: UUID


class LocationWithoutParents(BaseModel):
    """親なし単文の位置情報."""

    user: UserReadPublic
    folders: list[UidStr]
    resource: MResource
    headers: list[UidStr]

    # for debug
    # def __str__(self) -> str:
    #     return f"{self.user.username} {'>'.join([h.val for h in self.headers])}"


class TanbunContext(LocationWithoutParents):
    """ある場所から単文へ至る文脈."""

    parents: list[Tanbun]


class TanbunLocation(TanbunContext):
    """定義元と引用先を含む単文の位置情報."""

    quote_contexts: list[TanbunContext] = Field(default_factory=list)


class TanbunAdjacency(BaseModel):
    """周辺情報も含める."""

    center: Tanbun
    details: list[Tanbun]
    premises: list[Tanbun]
    conclusions: list[Tanbun]
    refers: list[Tanbun]
    referreds: list[Tanbun]
    abstracts: list[Tanbun]
    examples: list[Tanbun]

    def __str__(self) -> str:
        """For display in CLI."""
        s = str(self.center)
        s += f"@{self.center.when()}"
        s += "\n" + str(self.center.stats) if self.center.stats else ""
        if self.details:
            s += f"  {{ {', '.join(map(str, self.details))} }}"
        if self.premises:
            s += f"\n<- {', '.join(map(str, self.premises))}"
        if self.conclusions:
            s += f"\n-> {', '.join(map(str, self.conclusions))}"
        if self.refers:
            s += f"\n<< {', '.join(map(str, self.refers))}"
        if self.referreds:
            s += f"\n>> {', '.join(map(str, self.referreds))}"
        return s


class TanbunChain(BaseModel):
    """詳細."""

    uid: UUID
    g: NXGraph
    knowdes: dict[str, Tanbun]
    location: TanbunLocation

    # テスト用メソッド
    def get(self, sentence: str) -> str:  # noqa: D102
        for k, v in self.knowdes.items():
            if v.sentence == sentence:
                if v.uid.hex not in self.g:
                    msg = "TanbunChain.get: not found uid in graph"
                    raise ValueError(msg)
                return k

        msg = f"'{sentence}' not found in tanbun detail"
        raise ValueError(msg)

    def succ(self, sentence: str, t: EdgeType) -> list[Tanbun]:  # noqa: D102
        uid = self.get(sentence)
        succs = list(t.succ(self.g, uid))
        return [self.knowdes[s] for s in succs]

    def pred(self, sentence: str, t: EdgeType) -> list[Tanbun]:  # noqa: D102
        uid = self.get(sentence)
        preds = list(t.pred(self.g, uid))
        return [self.knowdes[s] for s in preds]

    def part(self, tgt: str) -> set[Tanbun]:
        """targetも含めて返す."""
        uid = self.get(tgt)

        is_first = True

        def succ(g: DiGraph, n: Hashable) -> Iterable[Hashable]:
            nonlocal is_first
            it = itertools.chain(
                EdgeType.BELOW.succ(g, n),
                EdgeType.SIBLING.succ(g, n) if not is_first else [],
            )
            is_first = False
            return it

        ns = to_nodes(self.g, uid, succ)
        return {self.knowdes.get(s) for s in ns if s is not None}

    def relabeled(self) -> DiGraph:
        """UUIDstr node を単文に置き換えたgraphを返す."""
        return nx.relabel_nodes(self.g, self.knowdes)


class TanbunChains(RootModel[list[TanbunChain]]):
    """単文チェーンたち."""

    def get(self, sentence: str) -> TanbunChain:
        """単文で返す."""
        for chain in self.root:
            uid = chain.uid
            kn = chain.knowdes[uid.hex]
            if kn.sentence == sentence:
                return chain
        msg = f"'{sentence}' not found in tanbun chains"
        raise KeyError(msg)
