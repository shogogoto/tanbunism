"""tree変換."""

from __future__ import annotations

import re

from lark import Token, Transformer

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.quoterm.domain import Quoterm
from tanbun.feature.parsing.primitive.template import Template
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.term.const import BRACE_MARKER
from tanbun.feature.parsing.primitive.time import WhenNode
from tanbun.feature.parsing.sysnet.sysnode import Def, KNArg
from tanbun.feature.parsing.tree2net.lineparse import parse_line

ELLIPSIS_PLACEHOLDER = re.compile(r"(?:\.{3,}|…+)")


def _stoken(tok: Token, erase: str | None = None) -> Token:
    """Strip token."""
    v = tok
    if erase:
        v = v.replace(erase, "")
    return Token(type=tok.type, value=v.strip())


class TSysArg(Transformer):
    """to SysArg transformer."""

    THUS = lambda _, _tok: EdgeType.TO.forward  # noqa: E731
    CAUSE = lambda _, _tok: EdgeType.TO.backward  # noqa: E731
    EXAMPLE = lambda _, _tok: EdgeType.EXAMPLE.forward  # noqa: E731
    GENERAL = lambda _, _tok: EdgeType.EXAMPLE.backward  # noqa: E731

    REF = lambda _, _tok: EdgeType.REF.forward  # noqa: E731
    WHEN = lambda _, _tok: EdgeType.WHEN.forward  # noqa: E731

    WHERE = lambda _, _tok: EdgeType.WHERE.forward  # noqa: E731
    BY = lambda _, _tok: EdgeType.BY.forward  # noqa: E731

    ANTONYM = lambda _, _tok: EdgeType.ANTI.both  # noqa: E731
    SIMILAR = lambda _, _tok: EdgeType.SIMILAR.both  # noqa: E731
    DUPLICABLE = lambda _, _tok: Duplicable(n=_stoken(_tok))  # noqa: E731
    QUOTERM = lambda _, _tok: Quoterm(n=_stoken(_tok))  # noqa: E731
    TIME = lambda _, _tok: WhenNode.of(n=_stoken(_tok))  # noqa: E731

    # Resources
    AUTHOR = lambda _, _tok: _stoken(_tok, "@author")  # noqa: E731
    PUBLISHED = lambda _, _tok: _stoken(_tok, "@published")  # noqa: E731
    URL = lambda _, _tok: _stoken(_tok, "@url")  # noqa: E731

    @staticmethod
    def ONELINE(tok: Token) -> KNArg:  # noqa: N802 D102
        v = "".join(tok.split("   "))  # 適当な\nに対応する空白
        return parse_sysarg(v)

    @staticmethod
    def MULTILINE(tok: Token) -> KNArg:  # noqa: N802 D102
        sp = tok.split("\\\n")
        v = ""
        for s in sp:
            v += s.lstrip()
        return parse_sysarg(v)


def parse_sysarg(v: str) -> KNArg:
    """本文1行を、構文木で利用するノードへ変換する."""
    if Template.is_parsable(v):
        return Template.parse(v)

    alias, names, sentence = parse_line(v)
    if sentence is None:
        t = Term.create(*names, alias=alias)
        return Def.dummy(t)
    if alias is None and len(names) == 0:
        if ELLIPSIS_PLACEHOLDER.fullmatch(sentence) is not None:
            return Duplicable(n=sentence)
        return sentence
    definition_sentence = sentence
    if BRACE_MARKER.pattern.fullmatch(sentence) is not None:
        definition_sentence = Duplicable(n=sentence)
    return Def.create(definition_sentence, names, alias)
