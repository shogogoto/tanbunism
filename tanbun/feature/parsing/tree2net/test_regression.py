"""test."""

import pytest

from tanbun.feature.parsing.primitive.term.errors import MarkUncontainedError
from tanbun.feature.parsing.tree2net import parse2net


def test_alias_resolve_error() -> None:
    """エラー再現(alias引用でMarkUncontainedError)."""
    s = """
    # x
        AU |{アリストテレス}の宇宙: 月より内側が変化する世界、外側は不可侵の永久不変領域
          <-> ケプラーの超新星: 急に現れて天文学者たちを驚かせた
            when. 1604/10/9 ~
            ガリレオは視差が月より小さいことより{ケプラーの超新星}が月の外側にあると証明
              {AU}が誤りであることを決定づけた
    """
    with pytest.raises(MarkUncontainedError):
        _sn = parse2net(s)  # アリストテレス が未定義


def test_regression() -> None:
    """再現."""
    s = """
    # x
      選択公理: 空でない集合各々...
        空でない自然数の集合でも各集合の最小の要素を選べば自明に真
        無限集合で選び方が定義出来ない場合に自明ではない
          -> 他の公理から{選択公理}を証明できないか議論が巻き怒った
      構成主義: 実在するものは全て明示的に構成可能でなければならない
        {選択公理}を非難
      連続体仮説: 自然数の濃度と実数の濃度の中間は存在しない
      複数の妥当な集合論が成立
        <- {選択公理}と{連続体仮説}は独立に成り立つ
          by. ポール・コーエン: アメリカの数学者
            when. 1934 ~ 2007
      神の公理, 選択公理: 神でもなければ到底実行できない操作をできるとする

    """
    _sn = parse2net(s)


def test_regression2() -> None:
    """bbbaaaaaの用語が複数ヒットする場合のエラー再現."""
    s = """
    # title1
        @author John Due
        @published H20/11/1
    ## xxx
        A: a
        B: b
        C: c
        aA: aaa
        D: bbbaaaaa
    """
    _sn = parse2net(s)


def test_resource_metadata_accepts_legacy_colon_separator() -> None:
    """Legacy colon-separated metadata remains importable."""
    source = """
    # title
        @author: John Due
        @published: 2016-7-10
        @url: https://example.com/book
    ## section
        sentence
    """

    network = parse2net(source)

    assert {node.type: str(node) for node in network.meta} == {
        "AUTHOR": "John Due",
        "PUBLISHED": "2016-7-10",
        "URL": "https://example.com/book",
    }
