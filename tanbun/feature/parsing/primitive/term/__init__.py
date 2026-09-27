"""用語モデル."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from functools import cached_property, reduce
from typing import NoReturn, Self

import networkx as nx
from more_itertools import flatten
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from tanbun.feature.parsing.primitive.dupchk import DuplicationChecker
from tanbun.feature.parsing.primitive.term.const import (
    BRACE_MARKER,
)

from .errors import (
    AliasContainsMarkError,
    TermConflictError,
    TermMergeError,
)


def eq_term(t1: Term, t2: Term) -> bool:
    """比較."""
    return set(t1.names) == set(t2.names) and t1.alias == t2.alias


class Term(BaseModel, frozen=True):
    """用語."""

    names: tuple[str, ...] = Field(default_factory=tuple)
    alias: str | None = Field(
        default=None,
        title="別名",
        description="参照用の無意味な記号(参照を持たない)",
    )

    # rep: str = Field(default="", title="代表名")

    @property
    def rep(self) -> str:
        """代表名."""
        if len(self.names) == 0:
            return ""
        return next(iter(self.names))

    @field_validator("names")
    @classmethod
    def _validate(cls, vs: list[str]) -> list[str]:
        """Validate duplication."""
        for k, v in Counter(vs).items():
            if v > 1:
                msg = f"{k}が複数回{v}宣言されています"
                raise ValueError(msg)
        return vs

    @field_validator("alias")
    @classmethod
    def _validate_alias(cls, v: str | None) -> str | None:
        """Validate duplication."""
        if v is not None and BRACE_MARKER.contains(v):
            msg = "aliasにMARK文字が含まれています"
            raise AliasContainsMarkError(msg, alias=v)
        return v

    @classmethod
    def create(cls, *names: str, alias: str | None = None) -> Self:  # noqa: D102
        return cls(names=names, alias=alias)

    def __repr__(self) -> str:
        """Class representation."""
        return f"Term({self})"

    def __str__(self) -> str:
        """Display for user."""
        subs = ", ".join(set(self.names) - {self.rep})
        if len(subs) > 0:
            subs = f"({subs})"
        al = ""
        if self.alias is not None:
            al = f"[{self.alias}]"
        return f"{self.rep}{subs}{al}"

    def has(self, *names: str) -> bool:
        """同じ名前を持つ."""
        s1 = set(self.names)
        s2 = set(names)
        common = s1.intersection(s2)
        return len(common) > 0

    def allows_merge(self, other: Term) -> bool:
        """合併を許すか否か."""
        if not self.has(*other.names):
            return False
        if set(self.names) == set(other.names):
            return False
        if self.alias and other.alias:
            return False
        only_self = len(self.names) == 1 or len(other.names) > 1
        only_other = len(self.names) > 1 or len(other.names) == 1
        return only_self or only_other

    @property
    def has_only_alias(self) -> bool:
        """aliasのみ."""
        return self.alias is not None and len(self.names) == 0

    def merge(self, other: Term) -> Term:
        """名前を併せた用語へ."""
        if not self.allows_merge(other):
            raise TermMergeError(self, other)
        s1 = set(self.names)
        s2 = set(other.names)
        diff = s2.difference(s1)
        return Term(
            names=self.names + tuple(diff),
            alias=self.alias,
        )

    def has_mark(self) -> bool:
        """参照{}を持つか否か."""
        if len(self.names) == 0:
            return False
        return any(BRACE_MARKER.contains(n) for n in self.names)

    @property
    def marktree(self) -> nx.DiGraph:
        """マークを全て返す."""
        g = nx.DiGraph()
        for n in self.names:
            marks = BRACE_MARKER.pick(n)
            atom = BRACE_MARKER.replace(n, *marks)
            g.add_node(atom)
            for m in marks:
                g.add_edge(atom, m)
            if self.alias is not None:
                g.add_edge(self.alias, atom)
        if self.alias is not None:
            g.add_node(self.alias)
        return g

    @property
    def marks(self) -> list[str]:
        """Flatten marks."""
        return list(
            flatten([BRACE_MARKER.pick(n) for n in self.names]),
        )

    def __lt__(self, other: Term) -> bool:  # noqa: D105
        return self.names < other.names


def term_dup_checker() -> DuplicationChecker:
    """用語重複チェッカー."""

    def _err(t: Term) -> NoReturn:
        msg = f"用語'{t}'が重複しています"
        raise TermConflictError(msg, names=t.names)

    return DuplicationChecker(err_fn=_err)


class MergedTerms(BaseModel, frozen=True):
    """マージした用語一覧."""

    terms: list[Term] = Field(default_factory=list, init=False)
    _chk: DuplicationChecker = PrivateAttr(default_factory=term_dup_checker)

    def __getitem__(self, i: int) -> Term:  # noqa: D105
        return self.terms[i]

    def __len__(self) -> int:
        """名前を持つ用語の数."""
        return len(self.terms)

    def add(self, *ts: Term) -> Self:
        """用語を追加する."""
        for t in ts:
            self._chk(t)
            self._merge_and_append(t)
        return self

    def _merge_and_append(self, t: Term) -> None:
        """共通する名を持つ用語があれば返す."""
        commons = [v for v in self.terms if t.has(*v.names)]
        match len(commons):
            case 0:
                self.terms.append(t)
            case 1:
                common = commons[0]
                self.terms.remove(common)
                self.terms.append(common.merge(t))
            case _:
                merged = reduce(lambda t1, t2: t1.merge(t2), [t, *commons])
                for c in commons:
                    self.terms.remove(c)
                self.terms.append(merged)

    @cached_property
    def frozen(self) -> frozenset[Term]:
        """Frozen merged terms."""
        return frozenset(self.terms)


def check_and_merge_term(terms: Iterable[Term]) -> MergedTerms:
    """重複チェック."""
    return MergedTerms().add(*sorted(terms))
