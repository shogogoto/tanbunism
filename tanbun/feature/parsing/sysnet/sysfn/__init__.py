"""sys系関数."""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Iterable
from typing import TYPE_CHECKING

from lark import Token

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.template import Template
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.time import WhenNode
from tanbun.feature.parsing.sysnet.errors import (
    SysNetNotFoundError,
    sentence_dup_checker,
)
from tanbun.feature.parsing.sysnet.sysnode import (
    Def,
    DummySentence,
    KNArg,
)

if TYPE_CHECKING:
    import networkx as nx


def to_template(vs: Iterable[Hashable]) -> list[Template]:
    """テンプレートのみを取り出す."""
    return [v for v in vs if isinstance(v, Template)]


def to_term(vs: Iterable[Hashable]) -> list[Term]:
    """termのみを取り出す."""
    terms = [n for n in vs if isinstance(n, Term)]
    return [*terms, *[v.term for v in vs if isinstance(v, Def)]]


def arg2sentence(n: KNArg) -> str | DummySentence:
    """関係のハブとなる文へ."""
    match n:
        case Template():
            return n
        # case Term():
        #     d = Def.dummy(n)
        #     return d.sentence
        case str() | Duplicable():
            return n
        case Def():
            return n.sentence
        case _:
            msg = f"{type(n)}: {n} is not allowed."
            raise TypeError(msg)


def to_sentence(vs: Iterable[Hashable]) -> list[str | Duplicable]:
    """文のみを取り出す."""
    defed = [v.sentence for v in vs if isinstance(v, Def)]
    stc = [*defed, *[v for v in vs if isinstance(v, (str, Duplicable))]]
    return [s for s in stc if not isinstance(s, WhenNode)]


def check_duplicated_sentence(
    vs: Iterable[Hashable],
    *,
    reusable: Iterable[Hashable] = (),
) -> None:
    """文の重複チェック.

    場所のように同一ノードを複数箇所から参照する値は ``reusable`` で除外する。
    """
    s_chk = sentence_dup_checker()
    reusable_counts = Counter(reusable)
    for s in to_sentence(vs):
        if isinstance(s, (DummySentence, Duplicable)):
            continue
        if isinstance(s, Token) and s.type == "QUOTERM":
            continue
        if reusable_counts[s] > 0:
            reusable_counts[s] -= 1
            continue
        s_chk(s)


def to_def(vs: Iterable[KNArg]) -> list[Def]:
    """文のみを取り出す."""
    return [v for v in vs if isinstance(v, Def)]


def get_ifdef(g: nx.DiGraph, n: Hashable) -> KNArg:
    """defがあれば返す."""
    if n not in g:
        msg = f"{n} is not in this graph."
        raise SysNetNotFoundError(msg)
    match n:
        case str() | Duplicable():
            term = EdgeType.DEF.get_pred_or_none(g, n)
            if term is None:
                return n
            return Def(term=term, sentence=n)
        case Term():
            s = EdgeType.DEF.get_succ_or_none(g, n)
            if s is None:
                return n
            return Def(term=n, sentence=s)
        case _:
            raise TypeError(n)
