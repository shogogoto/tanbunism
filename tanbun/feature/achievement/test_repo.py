"""test."""

from datetime import datetime, timedelta
from operator import attrgetter

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.datetime import TZ
from tanbun.feature.entry.resource.usecase import save_text
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
