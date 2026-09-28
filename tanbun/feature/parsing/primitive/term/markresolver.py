"""mark解決器."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import networkx as nx
from pydantic import BaseModel

from tanbun.feature.domain.graph import to_nested
from tanbun.feature.domain.graph.schema import NXGraph
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.term.const import (
    BRACE_MARKER,
    FORMULA_IGNORE_MARK,
)
from tanbun.feature.parsing.primitive.term.errors import MarkUncontainedError
from tanbun.feature.parsing.primitive.term.marklookup import to_lookup

if TYPE_CHECKING:
    from tanbun.feature.parsing.primitive.term import MergedTerms


class MarkResolver(BaseModel, frozen=True):
    """mark解決器."""

    g: NXGraph  # mark network
    lookup: dict[str, Term]  # {mark: Term}辞書

    @classmethod
    def create(cls, mt: MergedTerms) -> Self:
        """Create instance."""
        lookup = to_lookup(mt.frozen)
        g = nx.DiGraph()
        for t in mt.frozen:
            g = nx.compose(g, t.marktree)
        return cls(g=g, lookup=lookup)

    def sentence2marktree(self, s: str | Duplicable) -> dict[str, str | dict]:
        """任意の文字列を用語解決 mark dict tree.

        Return:
        ------
            {mark: {mark:{...:{}}}}

        """
        sentence = str(s)
        marks = BRACE_MARKER.pick(sentence, FORMULA_IGNORE_MARK)
        for m in marks:
            if m not in self.g:
                msg = f"'{m}'は用語として存在しません at '{sentence}'"
                raise MarkUncontainedError(msg)
        return {m: to_nested(self.g, m, lambda g, n: g.successors(n)) for m in marks}

    def mark2term(self, md: dict) -> dict[Term, dict]:
        """markに対応する用語に変換する.

        Return:
        ------
            {Term: {Term:{...:{}}}}

        """
        d = {}
        for k, v in md.items():
            t = self.lookup[k]
            if any(v):  # 空でない
                d[t] = self.mark2term(v)
            else:
                d[t] = v
        return d

    def term2marktree(self, t: Term) -> dict[str, str | dict]:
        """用語を用語解決."""
        marks = [k for k, v in self.lookup.items() if v == t]
        return {m: to_nested(self.g, m, lambda g, n: g.successors(n)) for m in marks}
