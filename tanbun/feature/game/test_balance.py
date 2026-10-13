"""敵能力の達成度帯成長率."""
# ruff: noqa: PLR2004

from math import ceil, log1p

import pytest

from .balance import GameBalance


@pytest.mark.parametrize("region", [0, 2, 4])
def test_region_growth_multiplies_power_adjusted_stats(region: int) -> None:
    """表示領域1を基準に、HP・攻撃力が別々の割合で成長する."""
    balance = GameBalance(
        enemy_variance_percent=0,
        region_hp_growth_percent=20,
        region_attack_growth_percent=10,
    )
    assert balance.enemy_stats(100, 0, region) == (
        ceil((20 + log1p(100)) * (1 + region * 0.2)),
        ceil((12 + log1p(100) * 0.5) * (1 + region * 0.1)),
    )


def test_zero_growth_ignores_legacy_additive_settings() -> None:
    """旧加算値が残っていても、成長率0なら領域を進んでも能力は同じ."""
    balance = GameBalance(
        region_hp=100,
        region_attack=100,
        region_hp_growth_percent=0,
        region_attack_growth_percent=0,
    )
    assert balance.enemy_stats(100, 0, 0, "enemy") == balance.enemy_stats(
        100,
        0,
        4,
        "enemy",
    )


def test_old_settings_receive_growth_defaults_without_reinterpreting_values() -> None:
    """保存済みの加算値は割合に変換せず、新しい既定成長率を適用する."""
    balance = GameBalance.model_validate({"region_hp": 5, "region_attack": 2})
    assert balance.region_hp_growth_percent == 20
    assert balance.region_attack_growth_percent == 20
    assert balance.enemy_stats(0, 0, 4) == (36, 22)
