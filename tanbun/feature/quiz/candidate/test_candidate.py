"""誤答肢."""

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.parsing.sysnet.sysnode import DUMMY_SENTENCE
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser

from .candidate import (
    list_candidates_by_radius,
    list_candidates_in_resource,
    list_top_scoring_candidates,
)


async def u() -> LUser:  # noqa: D103
    s = """
    # title2
        A: aaa
            xxxx
        B: bbb
        C: ccc
    """
    user = await LUser(email="select_option@ex.com").save()
    await save_text(user.uid, s)
    return user


@mark_async_test()
async def test_list_candidates_in_resource():
    """リソース内検索."""
    await u()
    sent = await LSentence.nodes.first(val="aaa")
    await LSentence(val=DUMMY_SENTENCE, resource_uid=sent.resource_uid).save()
    c = await list_candidates_in_resource([sent.uid])
    assert len(c) == 3  # noqa: PLR2004
    c = await list_candidates_in_resource([sent.uid], only_with_term=True)
    assert len(c) == 2  # noqa: PLR2004


@mark_async_test()
async def test_list_candidates_by_radius():
    """距離指定で誤答肢候補を列挙."""
    await u()
    sent = await LSentence.nodes.first(val="aaa")
    with pytest.raises(ValidationError):
        await list_candidates_by_radius([sent.uid], radius=-999)
    c = await list_candidates_by_radius([sent.uid], radius=1)
    assert len(c) == 2  # noqa: PLR2004
    c = await list_candidates_by_radius([sent.uid], radius=2)
    assert len(c) == 3  # noqa: PLR2004
    # 用語あり
    c = await list_candidates_by_radius([sent.uid], radius=1, only_with_term=True)
    assert len(c) == 1
    c = await list_candidates_by_radius([sent.uid], radius=2, only_with_term=True)
    assert len(c) == 2  # noqa: PLR2004


@mark_async_test()
async def test_list_top_scoring_candidates():
    """対象と特定の関係をもつ候補を列挙する."""
    await fx_u()
    sent = await LSentence.nodes.first(val="aaa")
    # 用語ありなのは5個
    res = await list_top_scoring_candidates(
        [sent.resource_uid],
        only_with_term=True,
    )
    assert len(res) == 5  # noqa: PLR2004

    # 単文全てで16個 (対象自信を含む)
    res = await list_top_scoring_candidates(
        [sent.resource_uid],
        only_with_term=False,
    )
    assert len(res) == 15 + 1
