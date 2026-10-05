"""公開クイズ検索のスコア・絞り込み・ページングを検証."""

from httpx import AsyncClient
from neomodel import adb
from starlette import status

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.listing.search import search_public_quizzes
from tanbun.feature.quiz.listing.test_repo import _create_quiz_set
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aregister

u = async_fixture()(fx_u)


@mark_async_test()
async def test_public_quiz_search_prioritizes_score_before_paging(u: LUser):
    """古い高スコアも先頭に並び、他人のクイズも検索できる."""
    high_ids = await _create_quiz_set(u.uid, "ccc")
    other = await aregister(email="public-quiz@example.com")
    low_ids = await _create_quiz_set(other.uid, "ccc1")
    result = await search_public_quizzes("", Paging(size=100))
    assert result.total == len(high_ids + low_ids)
    assert {item.quiz.quiz_id for item in result.data} == set(high_ids + low_ids)
    assert [item.target_score for item in result.data] == sorted(
        (item.target_score for item in result.data),
        reverse=True,
    )
    assert result.data[0].target_score > result.data[-1].target_score
    assert result.data[0].resource_name == "# title"
    first = await search_public_quizzes("", Paging(size=1))
    second = await search_public_quizzes("", Paging(page=2, size=1))
    assert first.total == second.total == len(high_ids + low_ids)
    assert first.data[0] == result.data[0]
    assert second.data[0] == result.data[1]


@mark_async_test()
async def test_public_quiz_search_terms_broken_and_inactive(u: LUser):
    """用語も検索し、退役参照と停止ユーザーを公開しない."""
    ids = await _create_quiz_set(u.uid, "ccc")
    result = await search_public_quizzes(" C ", Paging())
    assert result.total == len(ids)
    assert (await search_public_quizzes("Grimm's law", Paging())).total == 0
    await adb.cypher_query(
        "MATCH (q:Quiz {uid: $uid}) CREATE (q)-[:BROKEN_BY]->(:RetiredSentence)",
        params={"uid": ids[0].hex},
    )
    assert (await search_public_quizzes("", Paging())).total == len(ids) - 1
    u.is_active = False
    await u.save()
    assert (await search_public_quizzes("", Paging())).total == 0


@mark_async_test()
async def test_public_quiz_search_api_without_login(ac: AsyncClient, u: LUser):
    """匿名で閲覧でき、ページサイズは制限される."""
    ids = await _create_quiz_set(u.uid, "ccc")
    response = await ac.get("/quiz/search", params={"q": "ccc", "size": 1})
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["total"] == len(ids)
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["target_score"] > 0
    assert (
        await ac.get("/quiz/search?size=101")
    ).status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
