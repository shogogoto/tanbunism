"""日替わり推薦の選択と保持のテスト."""

from asyncio import gather
from datetime import date, timedelta

from tanbun.conftest import mark_async_test
from tanbun.feature.user.label import LUser

from .daily import Candidate, append_daily, load_daily, save_daily, select_daily


def test_daily_selection_is_stable_and_rotates() -> None:
    """同じ日・ユーザーでは安定し、別日では候補が入れ替わる."""
    candidates = [Candidate(str(index), "resource") for index in range(100)]
    limit = 20
    first = select_daily(candidates, "user:2026-10-05", limit)
    assert len(set(first)) == limit
    assert select_daily(list(reversed(candidates)), "user:2026-10-05", limit) == first
    assert select_daily(candidates, "user:2026-10-06", limit) != first


def test_daily_selection_spreads_resources() -> None:
    """大きいResourceだけが推薦を占めない."""
    candidates = [Candidate(str(index), "large", 100) for index in range(100)]
    candidates += [Candidate("small-1", "small-1"), Candidate("small-2", "small-2")]
    selected = select_daily(candidates, "today", 3)
    assert {"small-1", "small-2"} <= set(selected)


def test_daily_selection_respects_weights() -> None:
    """同じResource内では重みの高い候補を優先する."""
    candidates = [
        Candidate("preferred", "resource", 20),
        Candidate("recent", "resource", 0.05),
    ]
    winners = [select_daily(candidates, str(day), 1)[0] for day in range(100)]
    assert winners.count("preferred") > winners.count("recent")
    assert select_daily([], "today", 30) == []


def test_daily_selection_deduplicates_cross_resource_targets() -> None:
    """複数Resourceに跨る同じクイズも一度だけ選ぶ."""
    candidates = [
        Candidate("same", "a"),
        Candidate("same", "b"),
        Candidate("other", "b"),
    ]
    assert len(select_daily(candidates, "today", 20)) == len({
        c.uid for c in candidates
    })


@mark_async_test()
async def test_daily_set_persistence() -> None:
    """同日の最初のセットと過去の日を保持. 用途・ユーザーは分離."""
    user = await LUser(email="daily@example.com").save()
    other = await LUser(email="other-daily@example.com").save()
    day = date(2026, 10, 5)
    assert await load_daily(user.uid, "tanbuns", day) is None
    assert await save_daily(user.uid, "tanbuns", day, ["first"]) == ["first"]
    assert await save_daily(user.uid, "tanbuns", day, ["second"]) == ["first"]
    assert await load_daily(user.uid, "tanbuns", day) == ["first"]
    assert await load_daily(other.uid, "tanbuns", day) is None
    assert await load_daily(user.uid, "quizzes", day) is None
    tomorrow = day + timedelta(days=1)
    assert await load_daily(user.uid, "tanbuns", tomorrow) is None
    assert await save_daily(user.uid, "tanbuns", tomorrow, ["second"]) == ["second"]
    assert await save_daily(user.uid, "tanbuns", day, ["late-request"]) == ["first"]
    assert await load_daily(user.uid, "tanbuns", tomorrow) == ["second"]
    for offset in range(2, 8):
        await save_daily(
            user.uid,
            "tanbuns",
            day + timedelta(days=offset),
            [str(offset)],
        )
    assert await load_daily(user.uid, "tanbuns", day) is None
    assert await load_daily(user.uid, "tanbuns", tomorrow) == ["second"]


@mark_async_test()
async def test_concurrent_daily_sets_keep_first_winner() -> None:
    """複数タブから同時に選択しても、その日のセットが分裂しない."""
    user = await LUser(email="concurrent-daily@example.com").save()
    results = await gather(
        *(
            save_daily(user.uid, "tanbuns", date(2026, 10, 5), [str(index)])
            for index in range(5)
        ),
    )
    assert all(result == results[0] for result in results)


@mark_async_test()
async def test_append_preserves_order_and_resets_next_day() -> None:
    """追加も同時実行で重複せず、翌日は前日のIDを引き継がない."""
    user = await LUser(email="append-daily@example.com").save()
    day = date(2026, 10, 6)
    await save_daily(user.uid, "test", day, ["first"])
    results = await gather(
        *(append_daily(user.uid, "test", day, ["second", "second"]) for _ in range(5)),
    )
    assert all(result == ["first", "second"] for result in results)
    assert await append_daily(
        user.uid,
        "test",
        day + timedelta(days=1),
        ["new-day"],
    ) == ["new-day"]
