"""ユーザー本人の表示速度と管理画面の秒数設定."""

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .balance import GameBalance


@mark_async_test()
async def test_tempo_is_stored_per_authenticated_user(ac):
    """演出速度の保存は冒険・HP・他ユーザーに影響しない."""
    first = await aregister("tempo-first@example.com")
    second = await aregister("tempo-second@example.com")
    headers = await aauth_header(first.email)
    path = "/game/battle/preferences"
    assert (await ac.get(path, headers=headers)).json() == {"tempo": "medium"}
    assert (await ac.put(path, headers=headers, json={"tempo": "fast"})).json() == {
        "tempo": "fast",
    }
    assert (await ac.get(path, headers=headers)).json() == {"tempo": "fast"}
    assert (await ac.get(path, headers=await aauth_header(second.email))).json() == {
        "tempo": "medium",
    }
    assert (
        await ac.put(path, headers=headers, json={"tempo": "invalid"})
    ).status_code == 422  # noqa: PLR2004


def test_tempo_defaults_and_order_are_validated():
    """既存の設定にも初期値を適用し、逆順・無限・ゼロを拒否する."""
    balance = GameBalance()
    assert balance.battle_tempo_slow_seconds == 3  # noqa: PLR2004
    assert balance.battle_tempo_medium_seconds == 1
    assert balance.battle_tempo_fast_seconds == pytest.approx(0.5)
    for value in (0, float("inf"), 4):
        with pytest.raises(ValidationError):
            GameBalance(battle_tempo_fast_seconds=value)
