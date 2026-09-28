"""用語関連."""

from pytest_unordered import unordered

from tanbun.feature.domain.graph import to_nested
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable
from tanbun.feature.parsing.meta_parse import title_parse
from tanbun.feature.parsing.primitive.heading import get_heading_path, get_headings
from tanbun.feature.parsing.tree2net import parse2net


def test_duplicable() -> None:
    """重複可能な文."""
    s = r"""
        # h1
            1
                +++ dup1 +++
                +++ dup1 +++
                +++ dup1 +++
            2
    """
    sn = parse2net(s)
    assert sn.root == title_parse(s)


def test_same_location_can_be_reused() -> None:
    """同じ場所を複数の単文へ結び付けられる."""
    source = """
        # title
            サルトル: 哲学者
                where. フランス
            ルソー: 思想家
                where. フランス
    """

    network = parse2net(source)

    assert set(EdgeType.WHERE.pred(network.g, "フランス")) == {
        "哲学者",
        "思想家",
    }


def test_same_actor_can_be_reused() -> None:
    """同じ人物を複数の単文へ結び付けられる."""
    source = """
        # title
            帰納の懐疑論
                by. ヒューム
            因果関係への懐疑
                by. ヒューム
    """

    network = parse2net(source)

    assert set(EdgeType.BY.pred(network.g, "ヒューム")) == {
        "帰納の懐疑論",
        "因果関係への懐疑",
    }


def test_same_relation_target_can_be_reused() -> None:
    """同じ関係先を複数の単文から参照できる."""
    source = """
        # title
            帰納への懐疑
                <- ヒューム
            因果関係への懐疑
                <- ヒューム
    """

    network = parse2net(source)

    assert set(EdgeType.TO.succ(network.g, "ヒューム")) == {
        "帰納への懐疑",
        "因果関係への懐疑",
    }


def test_same_term_reference_can_define_multiple_terms() -> None:
    """同じ用語参照だけからなる定義文を異なる用語で共有できる."""
    source = """
        # title
            間接責任: 会社を介して責任を負う
            株式会社: {間接責任}
            有限会社: {間接責任}
    """

    network = parse2net(source)
    reference_sentences = [
        sentence for sentence in network.sentences if str(sentence) == "{間接責任}"
    ]
    expected_terms = {"株式会社", "有限会社"}

    assert len(reference_sentences) == len(expected_terms)
    assert all(isinstance(sentence, Duplicable) for sentence in reference_sentences)
    assert {
        network.get(sentence).term.rep for sentence in reference_sentences
    } == expected_terms
    assert all(
        network.get_resolved(sentence) == {"会社を介して責任を負う": {}}
        for sentence in reference_sentences
    )


def test_ellipsis_placeholders_can_be_repeated() -> None:
    """単独の省略記号は、文末の省略表現とは別の個別ノードにする."""
    source = """
        # title
            first
                ...
            second
                ...
            sentence ending ...
    """

    network = parse2net(source)
    placeholders = [
        sentence for sentence in network.sentences if str(sentence) == "..."
    ]

    assert len(placeholders) == len({"first", "second"})
    assert all(isinstance(sentence, Duplicable) for sentence in placeholders)
    assert "sentence ending ..." in network.sentences


def test_add_resolved_edge() -> None:
    """parse2net版."""
    s = r"""
        # h1
            A: df
            B: b{A}b
            C{B}: ccc
            D: d{CB}d
        ## h2
            P{D}: ppp
            Q: qqq
            X:
    """
    sn = parse2net(s)
    assert sn.get_resolved("df") == {}
    assert sn.get_resolved("b{A}b") == {"df": {}}
    assert sn.get_resolved("ccc") == {"b{A}b": {"df": {}}}
    assert sn.get_resolved("d{CB}d") == {"ccc": {"b{A}b": {"df": {}}}}
    assert sn.get_resolved("ppp") == {"d{CB}d": {"ccc": {"b{A}b": {"df": {}}}}}
    assert sn.get_resolved("qqq") == {}


def test_multiline() -> None:
    r"""\改行を1行に."""
    s = r"""
        # 1
            aaa\
                bbb
    """
    t = parse2net(s)
    assert "aaabbb" in t.g


def test_double_braces_are_literal_characters() -> None:
    """Python風の {{ / }} を用語参照ではなく文字として扱う."""
    source = """
        # title
            記述
                形態素はカッコ{{}} で囲む
    """

    network = parse2net(source)

    assert "形態素はカッコ{} で囲む" in network.sentences


def test_escaped_braces_can_surround_a_term_reference() -> None:
    """{{{term}}} は、参照記法を文字の波括弧で囲む."""
    source = """
        # title
            形態素: 言語の最小単位
            {{{形態素}}}で囲む
    """

    network = parse2net(source)

    sentence = "{{形態素}}で囲む"
    assert sentence in network.sentences
    assert network.get_resolved(sentence) == {"言語の最小単位": {}}


def test_extra_closing_brace_keeps_legacy_parser_behavior() -> None:
    """参照直後の余分な } を、エスケープ開始と誤認しない."""
    source = """
        # title
            密着軸: 顧客をよく知っている
            営業は{密着軸}}
    """

    network = parse2net(source)

    sentence = "営業は{密着軸}}"
    assert sentence in network.sentences
    assert network.get_resolved(sentence) == {"顧客をよく知っている": {}}


def test_add_block() -> None:
    """blockを正しく配置."""
    s = """
        # h1
            1
            2
                21
            3
                31
                32
            4
                41
                42
                    421
                43
                    431
                    432
                44
                    441
                    442
                        4421
                        4422
                        4423
    """
    sn = parse2net(s)
    assert list(EdgeType.BELOW.succ(sn.g, sn.root)) == ["1"]
    assert to_nested(sn.g, "1", EdgeType.SIBLING.succ) == {"2": {"3": {"4": {}}}}
    assert list(EdgeType.BELOW.succ(sn.g, "2")) == ["21"]
    assert to_nested(sn.g, "21", EdgeType.SIBLING.succ) == {}
    assert list(EdgeType.BELOW.succ(sn.g, "3")) == ["31"]
    assert to_nested(sn.g, "31", EdgeType.SIBLING.succ) == {"32": {}}
    assert list(EdgeType.BELOW.succ(sn.g, "4")) == ["41"]
    assert to_nested(sn.g, "41", EdgeType.SIBLING.succ) == {"42": {"43": {"44": {}}}}
    assert list(EdgeType.BELOW.succ(sn.g, "42")) == ["421"]
    assert to_nested(sn.g, "421", EdgeType.SIBLING.succ) == {}
    assert list(EdgeType.BELOW.succ(sn.g, "43")) == ["431"]
    assert list(EdgeType.BELOW.succ(sn.g, "44")) == ["441"]
    assert to_nested(sn.g, "441", EdgeType.SIBLING.succ) == {"442": {}}
    assert list(EdgeType.BELOW.succ(sn.g, "442")) == ["4421"]
    assert to_nested(sn.g, "4421", EdgeType.SIBLING.succ) == {"4422": {"4423": {}}}


def test_add_heading() -> None:
    """headingを正しく取得できる."""
    s = """
        !c00
        # h1
        !c0
            aaa
        !c1
            bbb
                ccc
                eee
                !c2
                    eEE
        !c2
                    eeE
        ## h2
        !c3
            fff
            ggg
                -> ddd
        ### h3
            hhh
                iii
                <- jjj
    """
    sn = parse2net(s)
    assert get_headings(sn.g) == {"# h1", "## h2", "### h3"}
    assert get_heading_path(sn.g, sn.root, "ccc") == ["# h1"]
    assert get_heading_path(sn.g, sn.root, "eEE") == ["# h1"]
    assert get_heading_path(sn.g, sn.root, "eeE") == ["# h1"]
    assert get_heading_path(sn.g, sn.root, "ddd") == ["# h1", "## h2"]
    assert get_heading_path(sn.g, sn.root, "iii") == ["# h1", "## h2", "### h3"]
    assert get_heading_path(sn.g, sn.root, "jjj") == ["# h1", "## h2", "### h3"]


def test_add_ctx() -> None:
    """文脈."""
    s = """
        # h1
            aaa
                <-> anti aaa\
                        aaa
            B: bbb
            C{B}: ccc
                -> D: d{CB}d
                    vvv
                    www
    """

    sn = parse2net(s)
    assert list(EdgeType.SIBLING.succ(sn.g, "bbb")) == ["ccc"]
    assert list(EdgeType.TO.succ(sn.g, "ccc")) == ["d{CB}d"]
    assert list(EdgeType.SIBLING.succ(sn.g, "ccc")) == []
    assert list(EdgeType.TO.pred(sn.g, "d{CB}d")) == ["ccc"]
    assert to_nested(sn.g, "d{CB}d", EdgeType.BELOW.succ) == {"vvv": {}}
    assert to_nested(sn.g, "vvv", EdgeType.SIBLING.succ) == {"www": {}}
    # 両方向
    assert list(EdgeType.ANTI.succ(sn.g, "aaa")) == ["anti aaaaaa"]
    assert list(EdgeType.ANTI.pred(sn.g, "anti aaaaaa")) == ["aaa"]


def test_replace_quoterm() -> None:
    """引用用語."""
    s = """
        # h1
            A: aaa
                aAA
                aBB
            B: bbb
        ## h2
            `A`
                ccc
                ddd
            `A`
                eee
                fff
    """

    sn = parse2net(s)
    assert to_nested(sn.g, "aaa", EdgeType.SIBLING.succ) == {"bbb": {}}
    assert sn.access("aaa", EdgeType.BELOW.succ) == unordered(["aAA", "ccc", "eee"])


def test_fullwidth_colon_defines_quoterm() -> None:
    """日本語の全角コロンでも用語を定義し引用できる."""
    s = """
        # h1
          parent
            -> 身体化\uff1a 体に覚えさせる
          exam
            <- `身体化`
    """

    sn = parse2net(s)

    assert "体に覚えさせる" in sn.g
    assert list(EdgeType.TO.pred(sn.g, "体に覚えさせる")) == ["parent"]


def test_template() -> None:
    """テンプレート."""
    s = r"""
        # h1
            f<a,b>: \diff{a, b}
            g<a>: !a!
            call g<a> -> _g<f<a, 1>>_
    """
    sn = parse2net(s)
    ret = [sn.expand(s) for s in sn.sentences]
    assert ret == [r"call g<a> -> !\diff{a, 1}!"]
