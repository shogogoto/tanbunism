"""構成要素となる部品."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from enum import StrEnum, auto
from operator import attrgetter
from typing import Final, Self

from pydantic import BaseModel

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import UUIDy
from tanbun.feature.parsing.primitive.mark import inject2placeholder
from tanbun.feature.parsing.sysnet.sysnode import Def
from tanbun.feature.quiz.errors import QuizOptionsMustBeDefError

from .rel import QuizRel


class QuizOption(BaseModel, frozen=True):
    """クイズ選択肢."""

    val: str | Def | Hashable
    rels: Sequence[QuizRel] | None = None

    @classmethod
    def create(  # noqa: D102
        cls,
        sentence: str,
        names: list[str] | None = None,
        rels: Sequence[QuizRel] | None = None,
    ) -> Self:
        val = Def.create(sentence, names=names)
        return cls(val=val, rels=rels)

    # @classmethod
    # def from_syselm(cls, elm: KNArg) -> Self:
    #     """SysNet要素から作成."""
    #     match elm:
    #         case Def():
    #             rels = EdgeType.path2edgetypes()
    #             return cls(val=elm, rels=elm.rels)
    #         # case
    #     return cls(val=elm, rels=elm.rels)

    @property
    def rels_stmt(self) -> str:
        """選択肢の関係の文言."""
        if self.rels is None:
            msg = "rel is None"
            raise ValueError(msg)
        return "の".join([str(r) for r in self.rels])

    @property
    def sentence(self) -> str:
        """単文表現."""
        if isinstance(self.val, Def):
            return str(self.val.sentence)
        return str(self.val)

    @property
    def terms(self) -> list[str]:
        """単文に定義された表示用の用語名."""
        if isinstance(self.val, Def):
            return list(self.val.term.names)
        return []

    @property
    def def_(self) -> Def:
        """クイズ対象."""
        tgt = self.val
        if isinstance(tgt, Def):
            return tgt
        msg = "クイズ対象が用語を持たない"
        raise QuizOptionsMustBeDefError(msg)


QUIZ_PLACEHOLDER = "$@"


S_SENT2TERM: Final = "に合う用語はどれ?"
S_TERM2SENT: Final = "に合う文はどれ?"
S_PAIR2REL: Final = "への関係を当ててください"
S_REL2PAIR: Final = "関係で繋がる単文を当ててください"
S_PAIR2REL_GRAPH: Final = "[]に入る関係はどれ?"
S_REL2PAIR_GRAPH: Final = "<?>に入る文はどれ?"


class QuizType(StrEnum):
    """問題文の種類."""

    SENT2TERM = auto()  # 用語当てクイズ: 単文の用語を当てる
    TERM2SENT = auto()  # 単文当てクイズ: 用語の単文を当てる
    PAIR2REL = auto()  # 関係クイズ: 単文のペアの関係を当てる. 関係の選択肢
    REL2PAIR = auto()  # ペアクイズ: 対象単文と特定の関係にある単文を当てる. 単文を列挙

    @classmethod
    def from_statemet(cls, stmt: str) -> QuizType:  # noqa: D102
        mapping = {
            S_SENT2TERM: cls.SENT2TERM,
            S_TERM2SENT: cls.TERM2SENT,
            S_REL2PAIR: cls.REL2PAIR,
            S_PAIR2REL: cls.PAIR2REL,
            S_REL2PAIR_GRAPH: cls.REL2PAIR,
            S_PAIR2REL_GRAPH: cls.PAIR2REL,
        }
        for suffix, quiz_type in mapping.items():
            if suffix in stmt:
                return quiz_type
        msg = f"Unknown statement: {stmt}"
        raise ValueError(msg)

    @property
    def _TEMPLATE(self) -> str:  # noqa: N802
        return {
            QuizType.SENT2TERM: f"文$@{S_SENT2TERM}",
            QuizType.TERM2SENT: f"用語$@{S_TERM2SENT}",
            QuizType.REL2PAIR: f"$@と$@{S_REL2PAIR}",
            QuizType.PAIR2REL: f"$@から$@{S_PAIR2REL}",
        }[self]

    @property
    def _OPT_ANSWER(self) -> str:  # noqa: N802
        return {
            QuizType.SENT2TERM: "def_.term",
            QuizType.TERM2SENT: "def_.sentence",
            QuizType.REL2PAIR: "sentence",
            QuizType.PAIR2REL: "rels_stmt",
        }[self]

    @property
    def _OPT_QUESTION(self) -> str:  # noqa: N802
        return {
            QuizType.SENT2TERM: "def_.sentence",
            QuizType.TERM2SENT: "def_.term",
            QuizType.REL2PAIR: "rels_stmt",
            QuizType.PAIR2REL: "sentence",
        }[self]

    @property
    def has_term(self) -> bool:
        """用語あり単文が選択肢."""
        return self in {QuizType.SENT2TERM, QuizType.TERM2SENT}

    def opt_answer(self, opt: QuizOption) -> str:
        """回答表現の選択肢."""
        return str(attrgetter(self._OPT_ANSWER)(opt))

    def correct_ids(
        self,
        target_id: UUIDy,
        correct_ids: list[UUIDy] | None = None,
    ) -> list[UUIDy]:
        """正解を決定する."""
        if correct_ids is None:
            correct_ids = []
        if self.has_term:
            return [str(target_id)]
        return [str(i) for i in correct_ids]

    def opt_question(self, opt: QuizOption) -> str:
        """問題文表現の選択肢."""
        return str(attrgetter(self._OPT_QUESTION)(opt))

    def statement(self, target: QuizOption, corrects: list[QuizOption]) -> str:
        """クイズの問題文."""
        if self.has_term:
            vals = [self.opt_question(target)]
            return self.inject(vals)
        correct = corrects[0]
        if self is QuizType.PAIR2REL:
            graph = _relation_graph(
                target.sentence,
                correct.sentence,
                correct.rels,
                conceal_relations=True,
            )
            return f"{graph}\n{S_PAIR2REL_GRAPH}"
        graph = _relation_graph(target.sentence, None, correct.rels)
        return f"{graph}\n{S_REL2PAIR_GRAPH}"

    def inject(self, vals: list[str]) -> str:
        """プレースホルダーを置き換えて返す."""
        return inject2placeholder(
            self._TEMPLATE,
            vals,
            QUIZ_PLACEHOLDER,
            surround_pre="'",
            surround_post="'",
        )


def _relation_graph(
    target: str,
    correct: str | None,
    rels: Sequence[QuizRel] | None,
    *,
    conceal_relations: bool = False,
) -> str:
    """Render a knowledge path while keeping its actual edge directions."""
    if not rels:
        msg = "relation quiz requires a relation"
        raise ValueError(msg)
    edges = [rel.edge for rel in rels]
    if all(is_forward for _, is_forward in edges):
        end = correct if correct is not None else "<?>"
        return _forward_graph(
            target,
            end,
            [edge for edge, _ in edges],
            conceal_relations=conceal_relations,
        )
    if all(not is_forward for _, is_forward in edges):
        start = correct if correct is not None else "<?>"
        return _forward_graph(
            start,
            target,
            [edge for edge, _ in reversed(edges)],
            conceal_relations=conceal_relations,
        )

    end = correct if correct is not None else "<?>"
    parts = [_graph_node(target)]
    for index, (edge, is_forward) in enumerate(edges):
        edge_name = "" if conceal_relations else edge.name
        arrow = f"-[{edge_name}]->" if is_forward else f"<-[{edge_name}]-"
        node = _graph_node(end) if index == len(edges) - 1 else "…"
        parts.extend((arrow, node))
    return "".join(parts)


def _forward_graph(
    start: str,
    end: str,
    edges: Sequence[EdgeType],
    *,
    conceal_relations: bool = False,
) -> str:
    parts = [_graph_node(start)]
    for index, edge in enumerate(edges):
        edge_name = "" if conceal_relations else edge.name
        node = _graph_node(end) if index == len(edges) - 1 else "…"
        parts.extend((f"-[{edge_name}]->", node))
    return "".join(parts)


def _graph_node(value: str) -> str:
    return value if value == "<?>" else f"'{value}'"
