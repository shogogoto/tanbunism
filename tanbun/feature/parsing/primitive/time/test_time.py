"""時系列.

# CLIで何を表示するか

# 期間
暗黙の期間 ex. 1000 -> 1000/01/01 ~ 1000/12/31 みたいな
# 月の期間を定義
    1000/1/1 ~  終わりが不定
    ~ 1111/11/11  始まりが不定
    1000/1/1 ~ 1111/11/11 決まってる


# **時系列でソート**


    重なった期間をどう表現するか
    intervaltree
        区間の重なり検索が高速
        メモリ効率が良い
        特に期間の重なりチェックに特化
"""

import pytest
from pytest_unordered import unordered

from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.primitive.time.errors import EndBeforeStartError

from . import Series, parse_when


@pytest.mark.parametrize(
    ("string", "expected"),
    [
        ("1000 ~", "1000/.."),
        ("~ 1200", "../1200"),
        ("12C ~", "1101/.."),
        ("~ 12C", "../1200"),
        ("1000 ~ 1200", "1000/1200"),
        ("1200~", "1200~"),
        ("1200 ~ 1300/12/1", "1200/1300-12-01"),
        ("17C ~ 18C", "1601/1800"),
        ("1604/10/9 ~", "1604-10-09/.."),
        ("BC1C", "-0099/0000"),
        ("BC0C", "0001/0100"),
        ("11C LATE ~ 13C MID", "1081/1261"),
        ("11C LATE ~", "1081/.."),
        ("~ 13C MID", "../1261"),
    ],
)
def test_parse_when(string: str, expected: str) -> None:
    """EDTFではなく独自のInterval."""
    when = parse_when(string)
    ex = parse_when(expected)
    assert when.lower_strict() == ex.lower_strict()
    assert when.upper_strict() == ex.upper_strict()


def test_invalid_interval() -> None:
    """不正な期間."""
    with pytest.raises(EndBeforeStartError):
        Series.create(["1831 ~ 79"])


@pytest.mark.parametrize(
    ("string", "start_year", "end_year"),
    [
        ("187X ~ 1900", 1870, 1900),
        ("-100000 ~ -35000", -100000, -35000),
        ("BC100000 ~ BC35000", -100000, -35000),
    ],
)
def test_parse_extended_year_interval(
    string: str,
    start_year: int,
    end_year: int,
) -> None:
    """不確定年代や5桁以上の紀元前年を期間の端点にできる."""
    when = parse_when(string)

    assert when.lower_strict().tm_year == start_year
    assert when.upper_strict().tm_year == end_year


def test_series() -> None:
    """時系列のソート."""
    dup1 = Duplicable(n="2004")
    dup2 = Duplicable(n="2004")
    series = Series.create(
        [
            "2000/1 ~ 2025",
            "2004/12",
            "2004",
            "2004/12",
            dup1,
            dup2,
            "2001",
            "1950/7/7",
            "1945",
            "-02XX",
            "2020",
        ],
    )
    assert series.overlap("2002 ~ 2005") == unordered(
        [
            "2000/1 ~ 2025",
            dup1,
            dup2,
            "2004",
            "2004/12",
        ],
    )
    assert series.overlap("2004/11") == unordered(["2000/1 ~ 2025", dup1, dup2, "2004"])
    assert series.overlap("2002") == ["2000/1 ~ 2025"]
    assert series.overlap("1945/1/2") == ["1945"]
    assert series.envelop("~ 1949") == ["-02XX", "1945"]
    assert series.envelop("2005 ~") == ["2020"]
