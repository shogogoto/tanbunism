"""系ネットワークのノード."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Hashable
from functools import cache
from typing import TYPE_CHECKING, Final, Self, override

from lark import Token
from pydantic import BaseModel

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.quoterm.domain import Quoterm
from tanbun.feature.parsing.primitive.template import Template
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.time import WhenNode
from tanbun.feature.parsing.sysnet.errors import DefSentenceConflictError

if TYPE_CHECKING:
    import networkx as nx


class IDef(BaseModel, ABC, frozen=True):
    """super定義."""

    def __repr__(self) -> str:  # noqa: D105
        return str(self)

    @abstractmethod
    def __str__(self) -> str:  # noqa: D105
        raise NotImplementedError

    @abstractmethod
    def add_edge(self, g: nx.DiGraph) -> None:
        """定義エッジ追加."""
        raise NotImplementedError


class Def(IDef, frozen=True):
    """定義."""

    term: Term
    sentence: str | Duplicable

    @classmethod
    def create(
        cls,
        sentence: str | Duplicable,
        names: list[str] | None = None,
        alias: str | None = None,
    ) -> Self:
        """便利コンストラクタ."""
        if names is None:
            names = []
        t = Term.create(*names, alias=alias)
        return cls(term=t, sentence=sentence)

    @override
    def __str__(self) -> str:
        return f"{self.term}: {self.sentence}"

    @classmethod
    @cache
    def dummy(cls, t: Term) -> Self:
        """Create vacuous def."""
        return cls(term=t, sentence=DummySentence())

    @classmethod
    def dummy_from(cls, *names: str, alias: str | None = None) -> Self:
        """Create vacuous def."""
        t = Term.create(*names, alias=alias)
        return cls.dummy(t)

    @override
    def add_edge(self, g: nx.DiGraph) -> None:
        """定義の追加."""
        add_def_edge(g, self.sentence, self.term)

    @property
    def is_dummy(self) -> bool:
        """文がダミー."""
        return isinstance(self.sentence, DummySentence)


def add_def_edge(g: nx.DiGraph, sentence: Hashable, term: Hashable) -> None:
    """定義の追加."""
    terms = list(EdgeType.DEF.pred(g, sentence))
    match len(terms):
        case 0:  # 新規登録
            EdgeType.DEF.add_edge(g, term, sentence)
        case _:
            msg = f"'{term}: {sentence}'が他の定義文と重複しています"
            raise DefSentenceConflictError(msg, terms)


DUMMY_SENTENCE: Final = "<<<not defined>>>"


class DummySentence(Duplicable, frozen=True):
    """Termのみの場合に擬似的に定義とみなすための空文字列."""

    n: str = DUMMY_SENTENCE


type Sentency = str | Duplicable | WhenNode | Quoterm
type _KNElem = Sentency | Template  # 共通型
type KNode = Term | _KNElem
type KNArg = Def | _KNElem


META_TYPES: Final = ["AUTHOR", "PUBLISHED", "URL"]


def is_meta(v: KNode) -> bool:
    """sysnetのメタ情報 ex. 著者とか."""
    return isinstance(v, Token) and v.type in META_TYPES
