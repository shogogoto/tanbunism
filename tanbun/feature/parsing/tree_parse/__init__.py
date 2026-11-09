"""テキストから構文木作成."""

from __future__ import annotations

from collections.abc import Hashable
from functools import lru_cache
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING, override

from lark import Lark, Transformer, Tree, UnexpectedInput
from lark.indenter import DedentError, Indenter

from tanbun.feature.parsing.tree_parse.errors import HEAD_ERR_EXS, UndedentError
from tanbun.feature.parsing.tree_parse.undent import detect_undent, front_pivot

if TYPE_CHECKING:
    from lark.visitors import TransformerChain


class SampleIndenter(Indenter):
    """sample."""

    @property
    @override
    def NL_type(self) -> str:
        return "_NL"

    @property
    @override
    def OPEN_PAREN_types(self) -> list[str]:
        return []

    @property
    @override
    def CLOSE_PAREN_types(self) -> list[str]:
        return []

    @property
    @override
    def INDENT_type(self) -> str:
        return "_INDENT"

    @property
    @override
    def DEDENT_type(self) -> str:
        return "_DEDENT"

    @property
    @override
    def tab_len(self) -> int:
        return 4


PARSER_CACHE_MAX_ENTRIES = 16


@lru_cache(maxsize=PARSER_CACHE_MAX_ENTRIES)
def create_parser(transformer: TransformerChain | Transformer | None = None) -> Lark:
    """直近の構文解析器だけを再利用し、変換器を無制限に保持しない."""
    return Lark(
        (Path(__file__).parent / "input.lark").read_text(),
        parser="lalr",
        postlex=SampleIndenter(),
        transformer=transformer,
        # keep_all_token=True,
    )


def parse2tree(
    text: str,
    transformer: TransformerChain | Transformer | None = None,
) -> Tree:
    """Parse and transform."""
    txt = dedent(text)
    p = create_parser(transformer)
    try:
        return p.parse(txt)  # , on_error=handle_error)
    except UnexpectedInput as e:
        exc_class = e.match_examples(
            p.parse,
            HEAD_ERR_EXS,
            token_type_match_fallback=True,
        )
        if not exc_class:
            raise
        s = e.get_context(txt)
        raise exc_class(s, e.line, e.column) from e
    except DedentError as e:
        lines = txt.splitlines()
        pivot, w = front_pivot(len(lines), len(lines))
        i = detect_undent(create_parser().parse, lines, pivot, w)

        min_ = max(0, i - 2)
        max_ = min(i + 2, len(lines))
        nums = range(min_, max_)
        digit = max(len(str(n)) for n in nums)
        arround = "\n".join([f"{n:>{digit}}: {lines[n]}" for n in nums])
        msg = f"Invalid indent was detected at line {i}. \n" + arround
        raise UndedentError(msg) from e


def get_leaves(tree: Tree) -> list[Hashable]:
    """leafを全て取得."""
    return list(tree.scan_values(lambda v: not isinstance(v, Tree)))
