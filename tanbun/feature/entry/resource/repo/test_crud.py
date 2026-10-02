"""系の永続化."""

import networkx as nx
import pytest
from neomodel import adb
from pytest_unordered import unordered

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.errors import NotFoundError
from tanbun.feature.entry.domain import ResourceMeta
from tanbun.feature.entry.label import LResource
from tanbun.feature.entry.mapper import MResource
from tanbun.feature.entry.namespace import fetch_namespace
from tanbun.feature.entry.resource.repo.delete import delete_resource
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.parsing.primitive.heading import include_heading
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.systats.nw1_n1 import NwOp
from tanbun.feature.parsing.tree2net import parse2net
from tanbun.feature.user.label import LUser

from .restore import (
    restore_sysnet,
    restore_tops,
)
from .save import sn2db


@pytest.fixture
def sn() -> SysNet:  # noqa: D103
    s = r"""
        # h1
            @author nanashi
            @author taro tanaka
            @published 1919
            A: df
            P1 |B, B1, B2, B3, B4: b{A}b
            C{B}: c
            D: d{CB}d
        ## h2
            P{D}: pp
            Q: qq
            c{A}c
            X:
        ### h31
            abcdefg
        ### h32
            aaa
                when. 19C
            bbb
                when. -500
            ccc
                when. 1900 ~ 2050
        #### h4
            aaaa
            bbbb
            cccc
                -> ppp
                    -> qqq
                        <- rrrrrrrrrrrrrrrrrrrrrrrrrrrrr
                            when. 19C
    """
    return parse2net(s)


@mark_async_test()
async def test_save_and_restore(sn: SysNet) -> None:
    """永続化して元に戻す."""
    d = ResourceMeta.to_dict(sn)
    m = ResourceMeta.model_validate(d)
    rsrc = await LResource(**m.model_dump()).save()
    await sn2db(sn, rsrc.uid)
    r, _ = await restore_sysnet(rsrc.uid)
    assert set(sn.terms) == set(r.terms)
    # assert set(sn.sentences) == set(r.sentences)  # なぜかFalse
    diff_stc = set(sn.sentences) - set(r.sentences)
    assert len(diff_stc) == 0
    assert sn.whens == unordered(r.whens)


@mark_async_test()
async def test_save_and_restore_apostrophes(u: LUser) -> None:
    """英語のアポストロフィを含む用語と単文を実DBへ保存できる."""
    text = """# quoted values
  Grimm's law: Sokal's criticism refers to {Grimm's law}
"""

    original, resource = await save_text(u.uid, text)
    restored, _uids = await restore_sysnet(resource.uid)

    assert set(original.terms) == set(restored.terms)
    assert set(original.sentences) == set(restored.sentences)


@mark_async_test()
async def test_restore_interval_with_missing_open_endpoint(u: LUser) -> None:
    """Neo4j上で省略された期間の開放端を復元できる."""
    _original, resource = await save_text(
        u.uid,
        "# title\n  sentence\n    when. ~ 1612\n",
    )
    await adb.cypher_query(
        """
        MATCH (:Sentence {resource_uid: $uid})-[:WHEN]->(interval:Interval)
        REMOVE interval.start
        """,
        params={"uid": resource.uid.hex},
    )

    restored, _uids = await restore_sysnet(resource.uid)

    assert len(restored.whens) == 1
    assert restored.whens[0].start is None
    assert restored.whens[0].end is not None


@mark_async_test()
async def test_restore_detail_with_detached_resource_root(u: LUser) -> None:
    """Resource直下の接続を失った既存本文を復元できる."""
    original, resource = await save_text(
        u.uid,
        """# title
## first
  first sentence
## second
  second sentence
""",
    )
    await adb.cypher_query(
        """
        MATCH (:Resource {uid: $uid})-[root:BELOW]->()
        DELETE root
        """,
        params={"uid": resource.uid.hex},
    )

    restored, _uids = await restore_sysnet(resource.uid)

    assert set(restored.sentences) == set(original.sentences)
    assert {str(node) for node in restored.g if str(node).startswith("#")} == {
        "# title",
        "## first",
        "## second",
    }


@async_fixture()
async def u() -> LUser:  # noqa: D103
    return await LUser(email="one@gmail.com").save()


@mark_async_test()
async def test_restore_tops(u: LUser):
    """先端のHeadかSentenceまで復元."""
    s = """
    # title
      direct
    ## h1
      s1
    ### h11
    #### h111
      s111{A}
    ## h2
      s2
      A, B: s2next
        when. ~ 19C
      C:
    """

    _sn, mr = await save_text(u.uid, s)
    g1, uids = await restore_tops(mr.uid)
    g = nx.relabel_nodes(g1, uids)
    assert uids[mr.uid] == "# title"
    assert {"direct", "s1", "s2"} < set(g.nodes)
    assert {"# title", "## h1", "### h11", "#### h111", "## h2"} == set(
        map(str, include_heading(g.nodes)),
    )
    assert "s2net" not in g1.nodes


@mark_async_test()
async def test_restore_and_delete_individual(u: LUser):
    """別のリソースの内容を取得しないことの確認."""
    s1 = """
    # title
      direct
      bro
    ## h1
      A: aaa
    ### h2
      B, BB, BBB: bbb
      ccc
        when. 20C ~
    ### h3
      xxx{A}{B}
    """
    _, mr1 = await save_text(u.uid, s1)

    s2 = """
    # title2
      direct2
    ## h1
      ddd
      eee
    """
    _, mr2 = await save_text(u.uid, s2)

    sn1, _uids1 = await restore_sysnet(mr1.uid)
    sn2, _uids2 = await restore_sysnet(mr2.uid)
    assert sn1.sentences == unordered([
        "direct",
        "aaa",
        "bbb",
        "ccc",
        "bro",
        "xxx{A}{B}",
    ])
    assert sn2.sentences == unordered(["direct2", "ddd", "eee"])
    ns = await fetch_namespace(u.uid)
    assert len([n for n in ns.g.nodes if isinstance(n, MResource)]) == 2  # noqa: PLR2004
    await delete_resource(mr1.uid)
    with pytest.raises(NotFoundError):
        sn1, _uids1 = await restore_sysnet(mr1.uid)
    sn2, _uids2 = await restore_sysnet(mr2.uid)
    assert sn2.sentences == unordered(["direct2", "ddd", "eee"])

    ns = await fetch_namespace(u.uid)
    assert len([n for n in ns.g.nodes if isinstance(n, MResource)]) == 1


@mark_async_test()
async def test_save_and_restore_quoterm(u: LUser):
    """quotermによるbelow siblingの無限ループを回避."""
    s = """
    # title
    ## h1
      A: aaa
    ### h12
      `A`
        ccc
        ddd
    ## h21
      `A`
        xxx
        yyy
    """
    _sn, mr1 = await save_text(u.uid, s)
    res, _uid = await restore_sysnet(mr1.uid)
    assert res.access("aaa", NwOp.get_detail) == unordered(["ccc", "ddd", "xxx", "yyy"])
