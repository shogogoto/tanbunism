"""復習実績は永続化し、知識構造は現在値から独立して計算する."""

from asyncio import gather
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from neomodel import adb
from starlette import status

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.dashboard.repo import record_tanbun_exposure
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.gamification.domain import LEVEL_CURVE, QUIZ_CORRECT_BONUS_XP
from tanbun.feature.gamification.resource import (
    fetch_resource_growth,
    record_resource_xp,
)
from tanbun.feature.quiz.answering.repo import create_answer
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aauth_header, aregister

u = async_fixture()(fx_u)


@mark_async_test()
async def test_power_excludes_detail_and_duplicate_edges(u: LUser):
    """同じ論理・参照を二重計上せず、Powerだけ再計算する."""
    await adb.cypher_query("""
        MATCH (a:Sentence {val:'ccc'}), (b:Sentence {val:'ccc1'})
        CREATE (a)-[:TO]->(b), (a)-[:TO]->(b),
            (a)-[:REF]->(b), (a)-[:RESOLVED]->(b), (a)-[:BELOW]->(b)
    """)
    [growth] = await fetch_resource_growth(u.uid)
    assert growth.logic_count > 0
    before = growth.power
    assert growth.power == growth.logic_count + growth.reference_count
    await adb.cypher_query("""
        MATCH (a:Sentence {val:'ccc'})-[edge:TO]->(:Sentence {val:'ccc1'})
        DELETE edge
    """)
    [after] = await fetch_resource_growth(u.uid)
    assert after.power == before - 1
    assert after.total_xp == growth.total_xp == 0


@mark_async_test()
async def test_exposure_xp_once_per_day_and_survives_sentence_deletion(u: LUser):
    """削除前の内容を保存し、翌日には再度XPを獲得できる."""
    sentence = await LSentence.nodes.first(val="ccc")
    today = date(2026, 10, 6)
    await record_tanbun_exposure(u.uid, sentence.uid, today)
    await record_tanbun_exposure(u.uid, sentence.uid, today)
    [first] = await fetch_resource_growth(u.uid)
    assert first.total_xp == 1
    await record_tanbun_exposure(u.uid, sentence.uid, today + timedelta(days=1))
    await adb.cypher_query(
        "MATCH (s:Sentence {uid:$uid}) DETACH DELETE s",
        params={"uid": sentence.uid},
    )
    [after] = await fetch_resource_growth(u.uid)
    assert after.total_xp == first.total_xp + 1
    assert after.recent_xp[0].subject == "ccc"


@mark_async_test()
async def test_answer_xp_once_per_quiz_day_and_per_user(u: LUser):
    """不正解から正解に進むとボーナスのみ追加し、他人の復習も分離する."""
    target = await LSentence.nodes.first(val="ccc")
    quiz = (
        await generate_quiz(QuizType.TERM2SENT, CandidateType.ALL, target.uid, 3, u.uid)
    ).to_readable()
    await create_answer(quiz.quiz_id, [quiz.distractors[0]], u.uid)
    [wrong] = await fetch_resource_growth(u.uid)
    await create_answer(quiz.quiz_id, quiz.correct, u.uid)
    await create_answer(quiz.quiz_id, quiz.correct, u.uid)
    [correct] = await fetch_resource_growth(u.uid)
    assert correct.total_xp == wrong.total_xp + QUIZ_CORRECT_BONUS_XP
    assert {event.source for event in correct.recent_xp} == {
        "quiz_answer",
        "correct_bonus",
    }
    other = await aregister("resource-learner@example.com")
    assert await fetch_resource_growth(other.uid) == []
    await create_answer(quiz.quiz_id, quiz.correct, other.uid)
    [other_growth] = await fetch_resource_growth(other.uid)
    assert other_growth.total_xp == correct.total_xp
    await adb.cypher_query(
        "MATCH (q:Quiz {uid:$uid}) DETACH DELETE q",
        params={"uid": quiz.quiz_id.hex},
    )
    [after] = await fetch_resource_growth(u.uid)
    assert after.total_xp == correct.total_xp


@mark_async_test()
async def test_resource_growth_api_private_and_import_has_no_xp(
    ac: AsyncClient,
    u: LUser,
):
    """Importでは加点せず、本人だけが復習ログを取得できる."""
    assert (
        await ac.get("/user/me/resource-growth")
    ).status_code == status.HTTP_401_UNAUTHORIZED
    await save_text(u.uid, "# another resource\n  added sentence\n")
    response = await ac.get(
        "/user/me/resource-growth",
        headers=await aauth_header(u.email),
    )
    assert response.is_success
    assert all(r["total_xp"] == 0 for r in response.json()["resources"])
    assert response.json()["rules"]["exposure_xp"] == 1


@mark_async_test()
async def test_xp_failure_rolls_back_exposure(
    u: LUser,
    monkeypatch: pytest.MonkeyPatch,
):
    """閲覧記録とXPが片方だけ保存されない."""

    async def fail(*args, **kwargs):  # noqa: RUF029
        msg = "xp failure"
        raise RuntimeError(msg)

    monkeypatch.setattr("tanbun.feature.dashboard.repo.record_exposure_xp", fail)
    target = await LSentence.nodes.first(val="ccc")
    with pytest.raises(RuntimeError, match="xp failure"):
        await record_tanbun_exposure(u.uid, target.uid, date(2026, 10, 6))
    rows, _ = await adb.cypher_query(
        "MATCH (e:TanbunExposure {user_id:$uid}) RETURN count(e)",
        params={"uid": to_uuid(u.uid).hex},
    )
    assert rows[0][0] == 0


@mark_async_test()
async def test_concurrent_resource_xp_is_idempotent(u: LUser):
    """複数リクエストでも、日別イベントは一つだけになる."""
    [growth] = await fetch_resource_growth(u.uid)
    await gather(*[
        record_resource_xp(
            u.uid,
            growth.resource_id,
            growth.resource_name,
            subject_id="same-subject",
            subject="snapshot",
            day="2026-10-06",
            source="tanbun_exposure",
            xp=1,
        )
        for _ in range(5)
    ])
    [after] = await fetch_resource_growth(u.uid)
    assert after.total_xp == 1
    assert len(after.recent_xp) == 1


@mark_async_test()
async def test_resource_level_and_power_are_independent(u: LUser):
    """Powerが低くても、到達XPは同じ曲線で算出する."""
    [growth] = await fetch_resource_growth(u.uid)
    await adb.cypher_query(
        """
        MATCH (r:Resource {uid:$resource_id})
        CREATE (:ResourceXpEvent {key:'level-test', user_id:$user_id,
            resource_id:r.uid, resource_name:r.title, xp:$xp,
            source:'tanbun_exposure', subject:'historical', earned_on:'2026-10-06'})
    """,
        params={
            "resource_id": growth.resource_id.hex,
            "user_id": to_uuid(u.uid).hex,
            "xp": LEVEL_CURVE,
        },
    )
    [after] = await fetch_resource_growth(u.uid)
    assert after.level == growth.level + 1
    assert after.current_level_xp == 0
    assert after.power == growth.power


@mark_async_test()
async def test_xp_failure_rolls_back_answer(u: LUser, monkeypatch: pytest.MonkeyPatch):
    """回答保存とXPを同一トランザクションで取り消す."""

    async def fail(*args, **kwargs):  # noqa: RUF029
        msg = "xp failure"
        raise RuntimeError(msg)

    monkeypatch.setattr("tanbun.feature.quiz.answering.repo.record_answer_xp", fail)
    target = await LSentence.nodes.first(val="ccc")
    quiz = (
        await generate_quiz(QuizType.TERM2SENT, CandidateType.ALL, target.uid, 3, u.uid)
    ).to_readable()
    with pytest.raises(RuntimeError, match="xp failure"):
        await create_answer(quiz.quiz_id, quiz.correct, u.uid)
    rows, _ = await adb.cypher_query(
        "MATCH (:User {uid:$uid})-[:ANSWER]->(a) RETURN count(a)",
        params={"uid": to_uuid(u.uid).hex},
    )
    assert rows[0][0] == 0
