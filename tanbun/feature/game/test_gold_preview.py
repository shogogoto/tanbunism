"""Gold試算は保存前の設定を使い、本番の計算と一致する."""

# ruff: noqa: PLR2004
import pytest

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .rewards import GoldPreviewRequest, GoldSettings, roll_gold


@pytest.mark.parametrize("formula", ["sqrt", "log"])
def test_preview_uses_real_reward_formula_and_does_not_mutate(formula):
    """実際の式で下限・中央・上限を確認する."""
    settings = GoldSettings(power_formula=formula)
    request = GoldPreviewRequest(settings=settings, power=2500, achievement=6)
    before = request.model_dump()
    result = request.preview()
    for source in ("enemy", "chest"):
        assert result[source] == {
            key: roll_gold(settings, 2500, 6, source, variation=value)
            for key, value in (("min", -1), ("typical", 0), ("max", 1))
        }
    assert request.model_dump() == before


@mark_async_test()
async def test_gold_preview_requires_admin(ac):
    """管理者だけが未保存の報酬設定を試算できる."""
    user = await aregister("gold-preview@example.com")
    headers = await aauth_header(user.email)
    path = "/admin/game/rewards/gold-preview"
    body = {"power": 100, "achievement": 1, "settings": GoldSettings().model_dump()}
    assert (await ac.post(path, json=body)).status_code == 401
    assert (await ac.post(path, json=body, headers=headers)).status_code == 403
    user.is_superuser = True
    await user.save()
    response = await ac.post(path, json=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == GoldPreviewRequest.model_validate(body).preview()
    body["power"] = -1
    assert (await ac.post(path, json=body, headers=headers)).status_code == 422
