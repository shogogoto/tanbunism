"""個人ダッシュボードAPIのテスト."""

from uuid import uuid4

from httpx import AsyncClient
from neomodel import adb
from starlette import status

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.user.testing import aauth_header, aregister

from .domain import (
    PersonalTanbunItem,
    TanbunExposureResult,
    TodayTanbunExposureCount,
)


@mark_async_test()
async def test_personal_tanbun_timeline_and_daily_exposure(ac: AsyncClient) -> None:
    """所有単文を表示し、同日の「見たよ」は1回だけ数える."""
    user = await aregister("dashboard-timeline@example.com")
    await save_text(
        user.uid,
        """
        # 新しい読書メモ
            再会: 学びは再会することで定着する
        """,
    )
    headers = await aauth_header(user.email)

    response = await ac.get("/dashboard/tanbuns", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    items = [PersonalTanbunItem.model_validate(item) for item in response.json()]
    target = next(item for item in items if "学びは再会" in item.sentence)
    assert target.term_names == ["再会"]
    assert target.score >= 0
    assert target.exposure_count == 0
    assert not target.seen_today

    detail = await ac.get(f"/tanbun/sentence/{target.uid}", headers=headers)
    assert detail.status_code == status.HTTP_200_OK

    first = await ac.post(
        f"/dashboard/tanbuns/{target.uid}/exposures",
        headers=headers,
    )
    second = await ac.post(
        f"/dashboard/tanbuns/{target.uid}/exposures",
        headers=headers,
    )
    first_result = TanbunExposureResult.model_validate(first.json())
    second_result = TanbunExposureResult.model_validate(second.json())
    assert first_result.recorded
    assert not second_result.recorded
    assert second_result.exposure_count == 1

    today = await ac.get("/dashboard/tanbuns/exposures/today", headers=headers)
    assert today.status_code == status.HTTP_200_OK
    assert TodayTanbunExposureCount.model_validate(today.json()).count == 1

    refreshed = await ac.get("/dashboard/tanbuns", headers=headers)
    refreshed_items = [
        PersonalTanbunItem.model_validate(item) for item in refreshed.json()
    ]
    refreshed_target = next(item for item in refreshed_items if item.uid == target.uid)
    assert refreshed_target.exposure_count == 1
    assert refreshed_target.seen_today


@mark_async_test()
async def test_personal_tanbun_timeline_requires_login(ac: AsyncClient) -> None:
    """個人TLは未ログインでは取得できない."""
    response = await ac.get("/dashboard/tanbuns")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@mark_async_test()
async def test_personal_tanbun_timeline_excludes_sentence_without_location(
    ac: AsyncClient,
) -> None:
    """resource_uidだけが残った孤立単文をTLへ表示しない."""
    user = await aregister("dashboard-orphan@example.com")
    _, resource = await save_text(
        user.uid,
        """
        # 現行の読書メモ
            現在位置を持つ知識
        """,
    )
    orphan_uid = uuid4().hex
    await adb.cypher_query(
        """
        CREATE (:Sentence {
            uid: $sentence_id,
            val: '位置を失った古い知識',
            resource_uid: $resource_id
        })
        """,
        params={"sentence_id": orphan_uid, "resource_id": resource.uid.hex},
    )
    headers = await aauth_header(user.email)

    response = await ac.get("/dashboard/tanbuns", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    ids = {item["uid"] for item in response.json()}
    assert orphan_uid not in ids


@mark_async_test()
async def test_personal_tanbun_timeline_keeps_daily_set(
    ac: AsyncClient,
) -> None:
    """Resourceを分散し、見たよを記録しても同日の順序を維持する."""
    user = await aregister("dashboard-rediscovery@example.com")
    for index in range(4):
        await save_text(
            user.uid,
            f"""
            # 読書メモ{index}
                再会する知識{index}
            """,
        )
    headers = await aauth_header(user.email)
    request_limit = 3

    response = await ac.get(
        f"/dashboard/tanbuns?limit={request_limit}",
        headers=headers,
    )

    assert response.status_code == status.HTTP_200_OK
    items = [PersonalTanbunItem.model_validate(item) for item in response.json()]
    assert len(items) == request_limit
    assert len({item.resource_uid for item in items}) == request_limit

    await ac.post(
        f"/dashboard/tanbuns/{items[0].uid}/exposures",
        headers=headers,
    )
    refreshed = await ac.get(
        f"/dashboard/tanbuns?limit={request_limit}",
        headers=headers,
    )
    refreshed_items = [
        PersonalTanbunItem.model_validate(item) for item in refreshed.json()
    ]
    assert [item.uid for item in refreshed_items] == [item.uid for item in items]
    assert refreshed_items[0].seen_today


@mark_async_test()
async def test_empty_daily_timeline_can_be_populated(ac: AsyncClient) -> None:
    """空セットの閲覧後に取り込んだ知識は、その日にも表示できる."""
    user = await aregister("dashboard-empty-daily@example.com")
    headers = await aauth_header(user.email)
    first = await ac.get("/dashboard/tanbuns", headers=headers)
    assert first.json() == []
    await save_text(user.uid, "# 初めての読書メモ\n  初めて出会う知識\n")
    populated = await ac.get("/dashboard/tanbuns", headers=headers)
    assert populated.status_code == status.HTTP_200_OK
    assert populated.json()
