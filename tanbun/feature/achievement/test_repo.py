"""test."""

from datetime import datetime, timedelta
from operator import attrgetter
from uuid import uuid4

import pytest
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.datetime import TZ
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.gamification.settings import (
    GamificationSettings,
    update_gamification_settings,
)
from tanbun.feature.gamification.usecase import (
    fetch_learning_progress,
    fetch_learning_progresses,
)
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.user.label import LUser

from .repo import (
    fetch_achievement_history,
    fetch_activity,
    fetch_user_with_current_achivement,
    snapshot_archivement,
)


async def setup(username: str, count: int) -> LUser:  # noqa: D103
    u = await LUser(
        email=f"{username}@example.com",
        display_name=username,
        username=username,
    ).save()

    ss = [
        f"""
            # title{i}
              @author author{i}
              @published 10{i}/1/1
              a{i}
              b{i}
        """
        for i in reversed(range(1, count + 1))
    ]

    for s in ss:
        await save_text(u.uid, s)
    return u


@async_fixture()
async def us() -> list[LUser]:  # noqa: D103
    return [
        await setup("zero", 0),
        await setup("one", 1),
        await setup("two", 2),
        await setup("three", 3),
    ]


@mark_async_test()
async def test_fetch_user_by_score(us: list[LUser]):
    """Aaaa."""
    res = await fetch_user_with_current_achivement()
    # paging
    res1 = await fetch_user_with_current_achivement(paging=Paging(page=1, size=3))
    assert len(res1.data) == 3  # noqa: PLR2004
    res2 = await fetch_user_with_current_achivement(paging=Paging(page=2, size=3))
    assert len(res2.data) == 1
    res3 = await fetch_user_with_current_achivement(paging=Paging(page=3, size=3))
    assert len(res3.data) == 0
    assert res.total == res1.total == res2.total == res3.total == 4  # noqa: PLR2004

    # ユーザー名検索
    res = await fetch_user_with_current_achivement("zero")
    assert len(res.data) == res.total == 1
    res = await fetch_user_with_current_achivement("four")
    assert len(res.data) == res.total == 0

    # ソート
    res = await fetch_user_with_current_achivement("", keys=["username"], desc=True)
    assert [r.user.username for r in res.data] == ["zero", "two", "three", "one"]
    res = await fetch_user_with_current_achivement("", keys=["username"], desc=False)
    assert [r.user.username for r in res.data] == ["one", "three", "two", "zero"]

    # ソート by archivement
    res = await fetch_user_with_current_achivement("", keys=["n_sentence"])
    assert [r.user.username for r in res.data] == ["three", "two", "one", "zero"]
    assert [r.archivement.n_resource for r in res.data] == [3, 2, 1, 0]
    assert all(row.level >= 1 for row in res.data)


@mark_async_test()
@pytest.mark.parametrize("coefficient", [10, 25])
async def test_search_level_matches_profile_ledger(coefficient: int) -> None:
    """見たよ・実際の加点・admin係数を使い、古い回答数は再加算しない."""
    reviewed = await setup("reviewed", 1)
    unread = await setup("unread", 1)
    await update_gamification_settings(
        GamificationSettings(level_xp_coefficient=coefficient),
    )
    await adb.cypher_query(
        """
        MATCH (u:User {uid: $uid})
        CREATE (u)-[:ANSWER]->(:Answer {uid: $answer, is_correct: true})
        WITH u
        UNWIND $events AS event
        CREATE (:ResourceXpEvent {
            key: event.key, user_id: u.uid, resource_id: $deleted_resource,
            source: event.source, xp: event.xp, earned_on: '2026-10-01'
        })
        """,
        params={
            "uid": reviewed.uid,
            "answer": uuid4().hex,
            "deleted_resource": uuid4().hex,
            "events": [
                {"key": uuid4().hex, "source": source, "xp": xp}
                for source, xp in [
                    ("tanbun_exposure", 60),
                    ("quiz_answer", 10),
                    ("correct_bonus", 4),
                    ("knowledge", 1000),
                ]
            ],
        },
    )
    profile = await fetch_learning_progress(reviewed.uid)
    batch = await fetch_learning_progresses([reviewed.uid, unread.uid])
    assert batch[reviewed.uid] == profile
    assert profile.total_xp == 74  # noqa: PLR2004
    assert profile.level == (4 if coefficient == 10 else 2)  # noqa: PLR2004
    assert batch[unread.uid].level == 1
    assert batch[unread.uid].total_xp == 0
    result = await fetch_user_with_current_achivement()
    levels = {row.user.id.hex: row.level for row in result.data}
    assert levels[reviewed.uid] == profile.level
    assert levels[unread.uid] == 1
    assert await fetch_learning_progresses([]) == {}


@mark_async_test()
async def test_snapshot_archivement(us: list[LUser]):
    """成果スナップショット."""
    assert await snapshot_archivement(paging=Paging(page=1, size=2)) == (4, 2, 2)
    res = await snapshot_archivement(paging=Paging(page=1, size=2))
    assert res == (4, 2, 0)  # 1回目で処理済み
    res = await snapshot_archivement(paging=Paging(page=2, size=2))
    assert res == (4, 2, 2)  # 残り
    res = await snapshot_archivement(paging=Paging(page=2, size=2))
    assert res == (4, 2, 0)  # 残りも処理済み
    res = await snapshot_archivement(paging=Paging(page=3, size=2))
    assert res == (4, 0, 0)  # 処理対象が範囲外

    # 翌週ならスナップショットを追加できる
    n = datetime.now(tz=TZ)
    assert await snapshot_archivement(n + timedelta(days=1)) == (4, 4, 0)
    assert await snapshot_archivement(n + timedelta(days=5)) == (4, 4, 0)
    assert await snapshot_archivement(n + timedelta(days=6, minutes=-10)) == (4, 4, 0)
    assert await snapshot_archivement(n + timedelta(days=6)) == (4, 4, 4)
    assert await snapshot_archivement(n + timedelta(days=7)) == (4, 4, 0)
    assert await snapshot_archivement(n + timedelta(days=11)) == (4, 4, 0)
    assert await snapshot_archivement(n + timedelta(days=12)) == (4, 4, 0)
    assert await snapshot_archivement(n + timedelta(days=13)) == (4, 4, 4)
    assert await snapshot_archivement(n + timedelta(days=14)) == (4, 4, 0)

    hs = await fetch_achievement_history([u.uid for u in us])
    zero = next(h for h in hs.root if h.user.username == "zero")
    assert zero.user.username == "zero"
    assert [a.n_resource for a in zero.archivements] == [0, 0, 0]
    three = next(h for h in hs.root if h.user.username == "three")
    assert three.user.username == "three"
    assert [a.n_resource for a in three.archivements] == [3, 3, 3]


@mark_async_test()
async def test_fetch_activity(us: list[LUser]):
    """現在の活動状況."""
    await snapshot_archivement(paging=Paging(page=1, size=100))
    u = await LUser(email="nolatest@ex.com").save()
    res = await fetch_activity([u.uid, us[0].uid])

    attrs = attrgetter("n_char", "n_sentence", "n_resource")

    no_latest = res.root[0]
    assert no_latest.latest is None
    assert attrs(no_latest.current) == (0, 0, 0)

    zero = res.root[1]
    assert zero.user.username == "zero"
    assert zero.latest is not None
    assert attrs(zero.latest) == (0, 0, 0)
    assert attrs(zero.current) == (0, 0, 0)
    await save_text(
        u.uid,
        """
        # heaven
          janne
          da
          arc
    """,
    )

    await save_text(
        us[0].uid,
        """
        # re:birth
          acid
          black
          cherry
    """,
    )

    # no_latestのまま currentが更新される
    res = await fetch_activity([u.uid, us[0].uid])
    no_latest = res.root[0]
    assert no_latest.latest is None
    assert attrs(no_latest.current) == (18, 3, 1)

    zero = res.root[1]
    assert zero.user.username == "zero"
    assert zero.latest is not None
    assert attrs(zero.latest) == (0, 0, 0)
    assert attrs(zero.current) == (25, 3, 1)

    # currentを更新
    await snapshot_archivement(
        now=datetime.now(tz=TZ) + timedelta(days=7),
        paging=Paging(page=1, size=100),
    )
    res = await fetch_activity([u.uid, us[0].uid])
    no_latest = res.root[0]
    assert attrs(no_latest.latest) == (18, 3, 1)
    assert attrs(no_latest.current) == (18, 3, 1)

    zero = res.root[1]
    assert zero.user.username == "zero"
    assert attrs(zero.latest) == (25, 3, 1)
    assert attrs(zero.current) == (25, 3, 1)
