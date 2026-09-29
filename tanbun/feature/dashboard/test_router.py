"""個人ダッシュボードAPIのテスト."""

from httpx import AsyncClient
from starlette import status

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.user.testing import aauth_header, aregister

from .domain import PersonalTanbunItem, TanbunExposureResult


@mark_async_test()
async def test_personal_tanbun_timeline_and_daily_exposure(ac: AsyncClient) -> None:
    """所有単文を表示し、同日の「見たよ」は1回だけ数える."""
    user = await aregister("dashboard-timeline@example.com")
    await save_text(
        user.uid,
        """
        # 新しい読書メモ
            学びは再会することで定着する
        """,
    )
    headers = await aauth_header(user.email)

    response = await ac.get("/dashboard/tanbuns", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    items = [PersonalTanbunItem.model_validate(item) for item in response.json()]
    target = next(item for item in items if "学びは再会" in item.sentence)
    assert target.exposure_count == 0
    assert not target.seen_today

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
async def test_personal_tanbun_timeline_includes_rediscovery_slot(
    ac: AsyncClient,
) -> None:
    """件数を絞っても、新着だけでなく古い未遭遇単文を再表示する."""
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
    assert items[0].updated_at >= items[1].updated_at
    assert items[2].updated_at <= items[1].updated_at
