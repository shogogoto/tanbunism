"""test."""

from uuid import UUID

import pytest
from pytest_unordered import unordered

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.graph import to_leaves, to_roots
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser

from . import fetch_tanbun_chains, tanbun_upper


@async_fixture()
async def u() -> LUser:  # noqa: D103
    return await LUser(email="one@gmail.com", username="one").save()


async def setup(u: LUser) -> SysNet:  # noqa: D103
    s = """
    # titleX
    ## head1
    ### head2
        parent
            when. 19C
            p1
            p2
                zero, re :0
                    when. R10/11/11
                    xxx
                        x1
                            x11
                            x12
                        x2
                            x21
                            x22
                            x23
                                x231
                    yyy
                    zzz
                    <- -1
                        when. 1919
                        <- -11
                        <- -12
                    <- -2
                        <- -21
                        <- -22
                            -> complex1
                                <- complex2
                    -> 1
                        -> 11
                        -> 12
                    -> 2
                        -> 21
                        -> 22
                            -> 221
        A: a
        B: b{A}b{zero}
        C: c{B}c
            -> ccc
    """
    sn, _r = await save_text(
        u.uid,
        s,
        path=("A", "B", "C.txt"),
    )  # C.txtはDBには格納されない
    return sn


@mark_async_test()
async def test_get_upper(u: LUser):
    """parent(resourceに辿れる)の末尾 upper を取得する."""
    _sn = await setup(u)

    async def s_assert(val: str, expected: str):
        s = await LSentence.nodes.get(val=val)
        upper = await tanbun_upper(UUID(s.uid))
        assert upper.val == expected

    # そのまま辿れるなら自身を返す
    await s_assert("0", "0")
    await s_assert("p2", "p2")
    await s_assert("x23", "x23")
    await s_assert("x231", "x231")
    await s_assert("yyy", "yyy")
    await s_assert("zzz", "zzz")
    # upperがない場合は自身を返す
    await s_assert("a", "a")
    await s_assert("c{B}c", "c{B}c")
    # -> の upperも辿れる
    await s_assert("1", "0")
    await s_assert("2", "0")
    await s_assert("11", "0")
    await s_assert("22", "0")
    await s_assert("221", "0")

    # <- の upperも辿れる
    await s_assert("-1", "0")
    await s_assert("-2", "0")
    await s_assert("-11", "0")
    await s_assert("-22", "0")
    # ->と<- の混在
    await s_assert("complex1", "0")
    await s_assert("complex2", "0")


@mark_async_test()
async def test_parents(u: LUser):
    """parentを取得(upstreamのbelow関係のみ、siblingは含まない."""


@mark_async_test()
async def test_detail_networks_to_or_resolved_edges(u: LUser):
    """IDによる詳細 TO/RESOLVED関係."""
    await setup(u)
    s = await LSentence.nodes.get(val="0")
    chains = await fetch_tanbun_chains([s.uid])
    c = chains.root[0]
    assert [k.sentence for k in c.succ("0", EdgeType.TO)] == unordered([
        "1",
        "2",
    ])
    roots_to = to_roots(c.g, EdgeType.TO)
    assert [c.knowdes[s].sentence for s in roots_to] == unordered([
        "-11",
        "-12",
        "-21",
        "-22",
        "complex2",
    ])
    leaves_to = to_leaves(c.g, EdgeType.TO)
    assert [c.knowdes[s].sentence for s in leaves_to] == unordered([
        "11",
        "12",
        "21",
        "221",
        "complex1",
    ])
    roots_ref = to_roots(c.g, EdgeType.RESOLVED)
    leaves_ref = to_leaves(c.g, EdgeType.RESOLVED)
    assert [c.knowdes[s].sentence for s in roots_ref] == unordered([
        "c{B}c",
    ])

    assert [c.knowdes[s].sentence for s in leaves_ref] == unordered([
        "0",
        "a",
    ])

    assert [p.sentence for p in c.part("0")] == unordered([
        "0",
        "xxx",
        "x1",
        "x11",
        "x12",
        "x2",
        "x21",
        "x22",
        "x23",
        "x231",
        "yyy",
        "zzz",
    ])

    loc = c.location
    assert loc.user.id == UUID(u.uid)
    assert [f.val for f in loc.folders] == ["A", "B"]
    assert loc.resource.name == "# titleX"
    assert [f.val for f in loc.headers] == ["## head1", "### head2"]
    assert [str(p) for p in loc.parents] == [
        "parentT(19C)",
        "p2",
    ]


@mark_async_test()
async def test_detail_no_below_no_header(u: LUser):
    """belowなしでも取得できるか."""
    s = """
    # titleX
        a
    """
    _sn, _r = await save_text(u.uid, s)
    s = await LSentence.nodes.get(val="a")
    chains = await fetch_tanbun_chains([s.uid])
    c = chains.root[0]
    assert [k.sentence for k in c.part("a")] == ["a"]
    assert c.location.headers == []
    assert c.location.user.id.hex == u.uid
    assert c.location.parents == []


@mark_async_test()
async def test_detail_no_below_no_header_with_parent(u: LUser):
    """belowなしでも取得できるか."""
    s = """
    # titleX
        parent
            a
    """
    _sn, _r = await save_text(u.uid, s)
    s = await LSentence.nodes.get(val="a")
    chains = await fetch_tanbun_chains([s.uid])
    c = chains.root[0]
    assert [k.sentence for k in c.part("a")] == ["a"]
    assert [k.sentence for k in c.location.parents] == ["parent"]
    assert c.location.headers == []
    assert c.location.user.id.hex == u.uid


@mark_async_test()
async def test_detail_no_header(u: LUser):
    """headerなし."""
    s = """
    # titleX
        a
            b
            c
        d
        e
            f
    """
    _sn, _r = await save_text(u.uid, s)
    s = await LSentence.nodes.get(val="a")
    chains = await fetch_tanbun_chains([s.uid])
    c = chains.root[0]
    assert [k.sentence for k in c.part("a")] == unordered(["a", "b", "c"])
    assert c.location.parents == []
    assert c.location.headers == []
    assert c.location.user.id.hex == u.uid


@mark_async_test()
async def test_chain_quoterm_rel(u: LUser):
    """quotermの関係も含めて返せるか確認."""
    s = """
        # title
            A: aaa
                -> direct
        ## h1
            `A`
                -> to1
                ex. ex1
                xe. ab1
                <- from1
        ## h2
            `A`
                xxx
                -> to2
    """

    _sn, _r = await save_text(u.uid, s)
    tgt = await LSentence.nodes.first(val="aaa")
    chains = await fetch_tanbun_chains([tgt.uid])
    c = chains.root[0]
    assert [k.sentence for k in c.succ("aaa", EdgeType.TO)] == unordered([
        "direct",
        "to1",
        "to2",
    ])
    assert [k.sentence for k in c.pred("aaa", EdgeType.TO)] == ["from1"]
    assert [k.sentence for k in c.succ("aaa", EdgeType.EXAMPLE)] == ["ex1"]
    assert [k.sentence for k in c.pred("aaa", EdgeType.EXAMPLE)] == ["ab1"]


@mark_async_test()
async def test_quote_contexts(u: LUser):
    """引用用語を置いた複数の親経路を、定義元とは別に返す."""
    s = """
        # title
            A: aaa
        ## quotes
            context1
                `A`
            context2
                middle
                    `A`
    """

    _sn, _r = await save_text(u.uid, s)
    target = await LSentence.nodes.get(val="aaa")
    chain = (await fetch_tanbun_chains([target.uid])).root[0]

    assert chain.location.parents == []
    assert [
        [parent.sentence for parent in context.parents]
        for context in chain.location.quote_contexts
    ] == unordered([
        ["context1"],
        ["context2", "middle"],
    ])


@mark_async_test()
async def test_fetch_multi_chains(u: LUser):
    """複数のチェーンを取得する."""
    s = """
        # title
            A: aaa
                bbb
                ccc
        ## h1
            parent
                xxx
                    -> ddd
                        -> eee
    """

    _sn, _r = await save_text(u.uid, s)
    tgt1 = await LSentence.nodes.first(val="aaa")
    tgt2 = await LSentence.nodes.first(val="ddd")
    chains = await fetch_tanbun_chains([tgt1.uid, tgt2.uid])
    assert len(chains.root) == 2  # noqa: PLR2004
    c_aaa = chains.get("aaa")
    c_ddd = chains.get("ddd")
    assert [p.sentence for p in c_aaa.part("aaa")] == unordered(["aaa", "bbb", "ccc"])
    assert [p.sentence for p in c_ddd.location.parents] == ["parent"]
    with pytest.raises(ValueError):  # noqa: PT011
        c_ddd.part("aaa")
    # nxprint(c_aaa.relabeled())
    # nxprint(c_ddd.relabeled())
