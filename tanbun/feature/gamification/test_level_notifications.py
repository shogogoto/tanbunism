"""確定済みの復習によるユーザー・リソースのレベルアップ通知."""

import json
from asyncio import gather
from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.dashboard.repo import record_tanbun_exposure
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.gamification.resource import (
    fetch_resource_growth,
    record_resource_xp,
)
from tanbun.feature.notification.domain import PushSubscriptionDraft
from tanbun.feature.notification.repo import list_notifications, save_push_subscription
from tanbun.feature.quiz.answering.repo import create_answer
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser

u = async_fixture()(fx_u)


async def seed_xp(u: LUser, user_xp: int, resource_xp: int) -> None:
    """通知せず既存の復習台帳を用意する."""
    [growth] = await fetch_resource_growth(u.uid)
    await adb.cypher_query(
        """UNWIND $events AS event
        CREATE (:ResourceXpEvent {key:event.key, user_id:$uid,
            resource_id:event.resource_id, xp:event.xp, source:'quiz_answer',
            subject:'historical', earned_on:'2026-10-06'})""",
        params={
            "uid": to_uuid(u.uid).hex,
            "events": [
                {
                    "key": "own",
                    "resource_id": growth.resource_id.hex,
                    "xp": resource_xp,
                },
                {
                    "key": "other",
                    "resource_id": uuid4().hex,
                    "xp": user_xp - resource_xp,
                },
            ],
        },
    )


@mark_async_test()
@pytest.mark.parametrize(
    ("user_xp", "resource_xp", "kinds"),
    [
        (9, 9, {"user_level_up", "resource_level_up"}),
        (29, 0, {"user_level_up"}),
        (19, 9, {"resource_level_up"}),
    ],
)
async def test_exposure_level_up_push_after_commit(
    u: LUser,
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_xp: int,
    resource_xp: int,
    kinds: set[str],
) -> None:
    """独立した閾値判定・実際のPush経路・再送による重複防止."""
    await seed_xp(u, user_xp, resource_xp)
    await save_push_subscription(
        u.uid,
        PushSubscriptionDraft(
            endpoint="https://push.example.test/level",
            keys={"p256dh": "test-public", "auth": "test-auth"},
        ),
    )

    def send(**kwargs):
        assert adb._active_transaction is None  # noqa: SLF001
        return kwargs

    push = AsyncMock(side_effect=send)
    monkeypatch.setattr("tanbun.feature.notification.usecase.webpush_async", push)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-private")
    target = await LSentence.nodes.first(val="ccc")
    await record_tanbun_exposure(u.uid, target.uid, date(2026, 10, 7))
    feed = await list_notifications(u.uid, limit=100)
    assert {n.kind.value for n in feed.notifications} == kinds
    assert push.await_count == len(kinds)
    for call in push.await_args_list:
        payload = json.loads(call.kwargs["data"])
        assert "Lv." in payload["body"]
        assert payload["url"].startswith(("/user/", "/resource/"))
    await record_tanbun_exposure(u.uid, target.uid, date(2026, 10, 7))
    assert push.await_count == len(kinds)
    assert len((await list_notifications(u.uid, limit=100)).notifications) == len(kinds)


@mark_async_test()
async def test_correct_bonus_level_up_and_failed_push_preserve_answer(
    u: LUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正解ボーナスで越えた閾値も通知し、通信失敗では回答・XPを失わない."""
    await seed_xp(u, 4, 4)
    push = AsyncMock(side_effect=RuntimeError("push offline"))
    monkeypatch.setattr("tanbun.feature.notification.usecase._dispatch_web_push", push)
    target = await LSentence.nodes.first(val="ccc")
    quiz = (
        await generate_quiz(
            QuizType.TERM2SENT,
            CandidateType.ALL,
            target.uid,
            3,
            u.uid,
        )
    ).to_readable()
    answer = await create_answer(quiz.quiz_id, quiz.correct, u.uid)
    assert answer.is_correct
    [growth] = await fetch_resource_growth(u.uid)
    assert growth.total_xp == 11  # noqa: PLR2004
    assert growth.level == 2  # noqa: PLR2004
    assert push.await_count == 2  # noqa: PLR2004
    await create_answer(quiz.quiz_id, quiz.correct, u.uid)
    assert push.await_count == 2  # noqa: PLR2004
    assert len((await list_notifications(u.uid, limit=100)).notifications) == 2  # noqa: PLR2004


@mark_async_test()
async def test_rollback_and_rule_change_do_not_send_level_up(
    u: LUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取り消した加点では通知も消え、係数変更は新たな達成と誤認しない."""
    await seed_xp(u, 9, 9)
    push = AsyncMock()
    monkeypatch.setattr("tanbun.feature.notification.usecase._dispatch_web_push", push)
    [growth] = await fetch_resource_growth(u.uid)
    with pytest.raises(RuntimeError, match="rollback"):  # noqa: PT012
        async with adb.transaction:
            await record_resource_xp(
                u.uid,
                growth.resource_id,
                growth.resource_name,
                subject_id="rolled-back",
                within_transaction=True,
                subject="review",
                day="2026-10-07",
                source="tanbun_exposure",
                xp=1,
            )
            assert len((await list_notifications(u.uid, limit=100)).notifications) == 2  # noqa: PLR2004
            msg = "rollback"
            raise RuntimeError(msg)
    assert not (await list_notifications(u.uid, limit=100)).notifications
    push.assert_not_awaited()
    await adb.cypher_query(
        "CREATE (:AdminSettings {key:'gamification', level_xp_coefficient:1})",
    )
    await record_resource_xp(
        u.uid,
        growth.resource_id,
        growth.resource_name,
        subject_id="new",
        subject="review",
        day="2026-10-07",
        source="tanbun_exposure",
        xp=1,
    )
    assert len((await list_notifications(u.uid, limit=100)).notifications) == 2  # noqa: PLR2004
    # 現在の係数でLv4→5のみ。以前のLv1からの遡及通知にはしない。
    assert all(
        "Lv. 4 → Lv. 5" in n.description
        for n in (await list_notifications(u.uid, limit=100)).notifications
    )


@mark_async_test()
async def test_concurrent_threshold_award_notifies_once(
    u: LUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値の直前に同時再送されても、XPと通知は各一回."""
    await seed_xp(u, 9, 9)
    push = AsyncMock()
    monkeypatch.setattr("tanbun.feature.notification.usecase._dispatch_web_push", push)
    [growth] = await fetch_resource_growth(u.uid)
    await gather(*[
        record_resource_xp(
            u.uid,
            growth.resource_id,
            growth.resource_name,
            subject_id="same",
            subject="review",
            day="2026-10-07",
            source="tanbun_exposure",
            xp=1,
        )
        for _ in range(5)
    ])
    [after] = await fetch_resource_growth(u.uid)
    assert after.total_xp == 10  # noqa: PLR2004
    assert push.await_count == 2  # noqa: PLR2004
    assert len((await list_notifications(u.uid, limit=100)).notifications) == 2  # noqa: PLR2004
