"""時系列.

時間の表現
    EDTF
    和暦
    期間の表現どうするか
        EDTF で 1950/2000 みたいに表現できるが分かりにくい気がする
        yyyy ~ yyyy
            がいい
        前者と後者をEDTFとして結合すればいい


"""

from __future__ import annotations

import time
from collections.abc import Hashable, Iterable
from datetime import date, datetime
from functools import cache
from typing import TYPE_CHECKING, Final, Self

import edtf
from intervaltree import Interval, IntervalTree
from more_itertools import flatten
from pydantic import BaseModel
from pyparsing import (
    Literal,
    ParserElement,
    Suppress,
    Word,
    printables,
)

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.time.errors import (
    EndBeforeStartError,
    ParseWhenError,
)
from tanbun.feature.parsing.primitive.time.parse import parse_extime, str2edtf

if TYPE_CHECKING:
    from edtf import EDTFObject

INTV_SEP: Final = "~"


@cache
def p_timespan() -> ParserElement:
    """Create a parser for patterns in the format."""
    edtf = Word("".join(c for c in printables if not c.isspace()))
    sep1, sep2, sep3 = (
        Suppress(Literal(s))
        for s in [
            f" {INTV_SEP} ",
            f"{INTV_SEP} ",
            f" {INTV_SEP}",
        ]
    )
    sep = sep1 | sep2 | sep3
    return edtf | edtf + sep | edtf + sep + edtf | sep + edtf


def parse_when(string: str) -> EDTFObject:  # noqa: PLR0911
    """時間指定(extimeの組み合わせ)をパース."""
    res = p_timespan().searchString(string)
    ls = list(flatten(res.asList()))
    if INTV_SEP not in ls:
        return parse_extime(string)
    i = ls.index(INTV_SEP)
    match (i, len(ls)):
        case (0, 2):  # ~ extime
            return _parse_open_start(ls[1])
        case (1, 2):  # ex1 ~
            return _parse_open_end(ls[0])
        case (1, 3):  # ex1 ~ ex2
            return _parse_between(ls[0], ls[2])
        case (0, 3):  # ~ ex season
            return _parse_open_start(f"{ls[1]} {ls[2]}")
        case (2, 3):  # ex1 season ~
            return _parse_open_end(f"{ls[0]} {ls[1]}")
        case (2, 5):  # ex1 season ~ ex2 season
            s1 = f"{ls[0]} {ls[1]}"
            s2 = f"{ls[3]} {ls[4]}"
            return _parse_between(s1, s2)
        case _:
            msg = f"{string}はフォーマット不正"
            raise ParseWhenError(msg)


def _parse_open_start(s1: str) -> EDTFObject:
    """~ ex のパース."""
    e = str2edtf(s1)
    if "/" in e:
        e = e.split("/")[1]
    return parse_extime(f"../{e}")


def _parse_open_end(s1: str) -> EDTFObject:
    """Ex ~ のパース."""
    s = str2edtf(s1)
    if "/" in s:
        s = s.split("/")[0]
    return parse_extime(f"{s}/..")


def _parse_between(s1: str, s2: str) -> EDTFObject:
    """2つのtの間."""
    f1 = str2edtf(s1)
    f2 = str2edtf(s2)
    e1 = parse_extime(f1)
    e2 = parse_extime(f2)
    lower = e1.lower if isinstance(e1, edtf.Interval) else e1
    upper = e2.upper if isinstance(e2, edtf.Interval) else e2
    return edtf.Interval(lower, upper)


class Series(BaseModel, arbitrary_types_allowed=True):
    """時系列."""

    tree: IntervalTree

    @classmethod
    def create(cls, whens: Iterable[Hashable]) -> Self:
        """Create from strings."""
        intvs = [Interval(*_when2span(str(s)), data=s) for s in whens]
        try:
            return cls(tree=IntervalTree(intvs))
        except ValueError as e:
            when = str(e).split(",")[-1].replace(")", "").strip()
            msg = "期間の終了が開始よりも早くて不正"
            raise EndBeforeStartError(msg, when) from e

    def overlap(self, when: str) -> list[Hashable]:
        """指定と重なる区間."""
        s, e = _when2span(when)
        intvs = sorted(self.tree.overlap(s, e))
        return [intv.data for intv in intvs]

    def envelop(self, when: str) -> list[Hashable]:
        """指定に含まれる区間."""
        s, e = _when2span(when)
        intvs = sorted(self.tree.envelop(s, e))
        return [intv.data for intv in intvs]

    @property
    def data(self) -> list[Hashable]:
        """全ての期間."""
        # t: IntervalTree = self.tree  # なぜか補間が聞かないself.treeの代わり
        intvs = sorted(self.tree.all_intervals)
        return [intv.data for intv in intvs]


class WhenNode(Duplicable, frozen=True):
    """時間ノード."""

    # Noneは無限
    start: float | None
    end: float | None

    def to_interval(self) -> Interval:
        """Intervalに変換."""
        start = self.start if self.start is not None else float("-inf")
        end = self.end if self.end is not None else float("inf")
        return Interval(start, end, data=self.n)

    @classmethod
    def of(cls, n: str) -> Self:
        """文字列から生成."""
        start, end = _when2span(n)
        return cls(n=n, start=start, end=end)


def _when2span(when: str) -> tuple[float, float]:
    obj = parse_when(when)
    s = obj.lower_strict()
    e = obj.upper_strict()
    if s == e:
        e = _nudge_offset(e)
    fs = float("-inf") if s == float("-inf") else time.mktime(s)
    fe = float("inf") if e == float("inf") else time.mktime(e)
    return fs, fe


def _nudge_offset(st: time.struct_time) -> time.struct_time:
    """同日のものをIntervalに変換するとstart == endとなってValueErrorになるのを回避."""
    return time.struct_time(
        (
            st.tm_year,
            st.tm_mon,
            st.tm_mday,
            23,
            59,
            59,
            st.tm_wday,
            st.tm_yday,
            st.tm_isdst,
        ),
    )


def parse2dt(s: str) -> date:
    """Convert from string to date."""
    st = parse_when(s.strip()).lower_strict()
    t = time.mktime(st)
    return datetime.fromtimestamp(t, tz=TZ).date()
