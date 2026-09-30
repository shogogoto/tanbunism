"""quiz domain."""

import uuid
from datetime import datetime
from random import Random
from textwrap import indent
from typing import Literal, Self
from uuid import UUID

from more_itertools import duplicates_everseen
from pydantic import BaseModel, Field, model_validator

from tanbun.feature.domain.datetime import TZ, Neo4jDateTime
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.quiz.domain.rel import QuizRel
from tanbun.feature.quiz.errors import (
    InvalidAnswerOptionError,
    QuizDuplicateError,
)

from .parts import QuizOption, QuizType


class QuizPromptRelation(BaseModel, frozen=True):
    """問題の対象から見た1辺の向きと、表示可能な関係名."""

    name: str | None
    is_forward: bool


class QuizPrompt(BaseModel, frozen=True):
    """UIが問題文を組み立てるための表示非依存データ."""

    subject: str
    object: str | None = None
    relations: list[QuizPromptRelation] = Field(default_factory=list)
    answer_kind: Literal["term", "sentence", "relation"]


class ReadableQuiz(BaseModel, frozen=True):
    """「読める状態」の問題文と選択肢を備えたクイズ."""

    # 既に読める状態の問題文や選択肢
    quiz_id: UUID
    quiz_type: QuizType
    prompt: QuizPrompt
    statement: str = Field(title="問題文")
    options: dict[str, str] = Field(title="選択肢")
    correct: list[str] = Field(title="正解")
    created: Neo4jDateTime
    no_correct_option: bool

    @property
    def type(self) -> QuizType:  # noqa: D102
        return self.quiz_type

    @property
    def distractors(self) -> list[str]:
        """誤答肢."""
        return [op for op in self.options if op not in self.correct]

    @property
    def string(self) -> str:
        """問題文."""
        s = f"{self.statement}\n"
        ops = [indent(op, "  * ") for op in self.options.values()]
        s += "\n".join(ops)
        return s

    def is_correct(self, selected: list[str]) -> bool:
        """正解かどうか."""
        for s in selected:
            if s not in self.options:
                msg = f"選択肢に存在しない回答; {s} not in {list(self.options.keys())}"
                raise InvalidAnswerOptionError(msg)
        s = set(selected)
        correct = set(self.correct)
        if self.no_correct_option:
            correct = set()
        return s == correct


class QuizSource(BaseModel, frozen=True):
    """クイズ生成のための情報源.

    便利なgetterを備えるのみ
    """

    quiz_id: UUID
    quiz_type: QuizType  # build方法を指定してくれる
    target_id: str  # 答えになるとは限らない
    correct_ids: list[str] = Field(default_factory=list)
    sources: dict[str, QuizOption] = Field(title="クイズの元となるメンバ")
    created: Neo4jDateTime
    no_correct_option: bool = Field(default=False)

    @model_validator(mode="after")
    def duplicate_check(self):
        """重複チェック."""
        srcs = list(self.sources.values())
        dups = list(duplicates_everseen(srcs))
        if len(dups) > 0:
            msg = f"同一のクイズ選択肢が指定されています: {dups}"
            raise QuizDuplicateError(msg)
        return self

    @property
    def target(self) -> QuizOption:
        """クイズ対象."""
        return self.sources[self.target_id]

    def get_id_by_sent(self, sent: str) -> str:
        """単文指定でidを返す."""
        key = next(
            (k for k in self.sources if self.sources[k].sentence == sent),
            None,
        )
        if key is None:
            msg = f"{sent} not found"
            raise KeyError(msg)
        return key

    def readable_options(self) -> dict[str, str]:
        """適切な選択肢をQuizごとに安定したランダム順で作成."""
        options = {k: self.quiz_type.opt_answer(v) for k, v in self.sources.items()}
        if not self.quiz_type.has_term:
            options = {k: v for k, v in options.items() if k != self.target_id}
        if self.no_correct_option:
            options = {k: v for k, v in options.items() if k not in self.correct_ids}
        items = list(options.items())
        Random(self.quiz_id.int).shuffle(items)  # noqa: S311 - 表示順のみ
        return dict(items)

    def to_readable(self) -> ReadableQuiz:
        """読める状態にする."""
        correct_opts = [self.sources[c] for c in self.correct_ids]
        return ReadableQuiz(
            quiz_id=self.quiz_id,
            quiz_type=self.quiz_type,
            prompt=self._prompt(correct_opts),
            statement=self.quiz_type.statement(self.target, correct_opts),
            options=self.readable_options(),
            correct=self.correct_ids,
            created=self.created,
            no_correct_option=self.no_correct_option,
        )

    def _prompt(self, corrects: list[QuizOption]) -> QuizPrompt:
        """表示層が文章を解析せずに描画できる構造化問題文を作る."""
        if self.quiz_type.has_term:
            answer_kind: Literal["term", "sentence", "relation"] = (
                "term" if self.quiz_type is QuizType.SENT2TERM else "sentence"
            )
            return QuizPrompt(
                subject=self.quiz_type.opt_question(self.target),
                answer_kind=answer_kind,
            )

        correct = corrects[0]
        if correct.rels is None:
            msg = "relation quiz requires a relation"
            raise ValueError(msg)
        conceal_relations = self.quiz_type is QuizType.PAIR2REL
        return QuizPrompt(
            subject=self.target.sentence,
            object=correct.sentence if conceal_relations else None,
            relations=[
                QuizPromptRelation(
                    name=None if conceal_relations else edge.name,
                    is_forward=is_forward,
                )
                for edge, is_forward in (relation.edge for relation in correct.rels)
            ],
            answer_kind="relation" if conceal_relations else "sentence",
        )

    @classmethod
    def from_sysnet(
        cls,
        sn: SysNet,
        qt: QuizType,
        target_stc: str,
        source_stcs: list[str],  # 順に番号が割り振られる
        correct_stcs: list[str] | None = None,
    ) -> Self:
        """SysNetから作成してテストを完結に書けるようにする."""
        if correct_stcs is None:
            correct_stcs = []
        tgt = sn.get(target_stc)

        ops: dict[str, QuizOption] = {}
        target_id = "dummy"
        correct_ids: list[str] = []
        for i, s in enumerate(source_stcs, start=1):
            src = sn.get(s)
            if tgt == src:
                rels = []
                target_id = str(i)
            else:
                ets, is_forward = EdgeType.path2edgetypes(sn.g, target_stc, s)
                rels = QuizRel.of(ets, is_forward)
            op = QuizOption(val=src, rels=rels)
            ops[str(i)] = op
            if s in correct_stcs:
                correct_ids.append(str(i))
        return cls(
            quiz_id=uuid.uuid4(),
            quiz_type=qt,
            target_id=target_id,
            sources=ops,
            correct_ids=correct_ids,
            created=datetime.now(tz=TZ),
        )
