"""公開学習進捗APIのテスト."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from neomodel import adb
from pytest_mock import MockerFixture

from tanbun.conftest import mark_async_test
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.user.testing import aregister

from .resource import fetch_resource_growth, record_resource_xp


@mark_async_test()
async def test_get_learning_progress_without_login(ac: AsyncClient) -> None:
    """公開プロフィールではログインせずLevelとXPを取得できる."""
    user = await aregister("learning-progress@example.com")
    await save_text(user.uid, "# knowledge is not review XP\n  imported sentence\n")

    response = await ac.get(f"/user/{user.uid}/learning-progress")

    assert response.is_success
    assert response.json()["level"] == 1
    assert response.json()["total_xp"] == 0
    assert response.json()["today_xp"] == 0
    assert response.json()["xp_details"] == [
        {
            "source": "tanbun_exposure",
            "activity_count": 0,
            "xp_per_activity": 1,
            "earned_xp": 0,
        },
        {
            "source": "quiz_answer",
            "activity_count": 0,
            "xp_per_activity": 5,
            "earned_xp": 0,
        },
        {
            "source": "correct_bonus",
            "activity_count": 0,
            "xp_per_activity": 2,
            "earned_xp": 0,
        },
    ]


@mark_async_test()
async def test_user_xp_sums_resource_ledger_and_japan_today(
    ac: AsyncClient,
    mocker: MockerFixture,
) -> None:
    """重複・他人・未移行の回答を除外し、台帳合計と今日のXPを返す."""
    mocker.patch(
        "tanbun.feature.gamification.usecase.datetime",
    ).now.return_value = datetime(2026, 10, 7, 0, 1, tzinfo=ZoneInfo("Asia/Tokyo"))
    user = await aregister("xp-ledger@example.com")
    other = await aregister("other-xp@example.com")
    _, first = await save_text(user.uid, "# first\n  sentence one\n")
    _, second = await save_text(user.uid, "# second\n  sentence two\n")
    for resource, day, source, amount, subject in [
        (first, "2026-10-06", "tanbun_exposure", 1, "seen-yesterday"),
        (first, "2026-10-07", "quiz_answer", 5, "answer"),
        (first, "2026-10-07", "correct_bonus", 2, "answer"),
        (second, "2026-10-07", "tanbun_exposure", 1, "seen-today"),
        (second, "2026-10-07", "tanbun_exposure", 1, "seen-today"),
    ]:
        await record_resource_xp(
            user.uid,
            resource.uid,
            resource.name,
            subject_id=subject,
            subject=subject,
            day=day,
            source=source,
            xp=amount,
        )
    await record_resource_xp(
        other.uid,
        first.uid,
        first.name,
        subject_id="other",
        subject="other",
        day="2026-10-07",
        source="quiz_answer",
        xp=5,
    )
    await adb.cypher_query(
        """MATCH (u:User {uid: $uid})
        CREATE (u)-[:ANSWER]->(:Answer {uid: $answer, is_correct: true})""",
        params={"uid": to_uuid(user.uid).hex, "answer": uuid4().hex},
    )
    response = await ac.get(f"/user/{user.uid}/learning-progress")
    data = response.json()
    assert (
        data["total_xp"]
        == sum(book.total_xp for book in await fetch_resource_growth(user.uid))
        == 9  # noqa: PLR2004
    )
    assert data["today_xp"] == 8  # noqa: PLR2004
    assert data["activity"]["n_quiz_answered"] == 1
    assert data["xp"]["quiz_answer"] == 5  # noqa: PLR2004
    await adb.cypher_query(
        "MATCH (r:Resource {uid: $uid}) DETACH DELETE r",
        params={"uid": first.uid.hex},
    )
    after = await ac.get(f"/user/{user.uid}/learning-progress")
    assert after.json()["total_xp"] == data["total_xp"]
    assert after.json()["today_xp"] == data["today_xp"]
