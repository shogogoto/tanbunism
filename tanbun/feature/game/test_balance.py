"""敵能力の達成度帯成長率."""
# ruff: noqa: PLR2004

from math import ceil, log1p, sqrt

import pytest
from pydantic import ValidationError

from .balance import Allocation, GameBalance


@pytest.mark.parametrize("region", [0, 2, 4])
@pytest.mark.parametrize("formula", ["log", "sqrt"])
def test_region_growth_multiplies_power_adjusted_stats(
    region: int,
    formula: str,
) -> None:
    """表示領域1を基準に、HP・攻撃力が別々の割合で成長する."""
    balance = GameBalance(
        power_formula=formula,
        power_reference=100,
        enemy_variance_percent=0,
        region_hp_growth_percent=20,
        region_attack_growth_percent=10,
    )
    scale = log1p(100) if formula == "log" else 1
    assert balance.enemy_stats(100, 0, region) == (
        ceil((20 + scale) * (1 + region * 0.2)),
        ceil((12 + scale * 0.5) * (1 + region * 0.1)),
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


@pytest.mark.parametrize("region", [0, 2, 4])
def test_enemy_speed_uses_power_growth_and_shared_variation(region: int):
    """Speed follows the same WYSIWYG formula as HP/attack."""
    balance = GameBalance(
        enemy_speed=10,
        power_speed=2,
        region_speed_growth_percent=15,
    )
    expected = ceil(
        (10 + sqrt(100) * 2) * (1 + region * 0.15) * balance.enemy_variation("enemy"),
    )
    assert balance.enemy_speed_stat(100, region, "enemy") == expected
    assert balance.enemy_speed_stat(100, region, "enemy") == expected


def test_speed_defaults_and_bounds():
    """Old settings still load and speed cannot reach zero or overflow."""
    balance = GameBalance.model_validate({"enemy_speed": 12})
    assert balance.power_speed == 0
    assert balance.region_speed_growth_percent == 0
    assert balance.enemy_speed_stat(1000, 20) == 12
    assert (
        GameBalance(enemy_speed=1, enemy_variance_percent=50).enemy_speed_stat(
            0,
            0,
            "e",
        )
        >= 1
    )
    assert (
        GameBalance(power_speed=100, region_speed_growth_percent=1000).enemy_speed_stat(
            10**9,
            100,
        )
        == 100000
    )


@pytest.mark.parametrize(("power", "hp"), [(100, 30), (1000, 52), (3000, 75)])
def test_sqrt_default_distinguishes_reading_resource_power(power, hp):
    """100〜3000のPowerでHPに分かる程度の差を出す."""
    balance = GameBalance(enemy_variance_percent=0)
    assert balance.enemy_stats(power, 0, 0)[0] == hp
    normalized = balance.model_copy(update={"power_reference": 100, "power_hp": 10})
    assert normalized.enemy_stats(power, 0, 0)[0] == hp


@pytest.mark.parametrize("formula", ["log", "sqrt"])
def test_power_scale_clamps_negative_and_zero_power(formula):
    """Powerの下限は両方式とも0として扱う."""
    balance = GameBalance(power_formula=formula)
    assert balance.enemy_power_scale(-100) == balance.enemy_power_scale(0) == 0


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_power_reference_is_rejected(value):
    """基準Powerは正の有限値に限定する."""
    with pytest.raises(ValidationError):
        GameBalance(power_reference=value)


def test_old_settings_receive_growth_defaults_without_reinterpreting_values() -> None:
    """保存済みの加算値は割合に変換せず、新しい既定成長率を適用する."""
    balance = GameBalance.model_validate({"region_hp": 5, "region_attack": 2})
    assert balance.region_hp_growth_percent == 20
    assert balance.region_attack_growth_percent == 20
    assert balance.enemy_stats(0, 0, 4) == (36, 22)


def test_legacy_time_allocation_is_ignored_and_cannot_extend_quiz_time() -> None:
    """旧クライアントの秒ポイントも読み飛ばし、持ち時間は管理値に固定する."""
    allocation = Allocation.model_validate({"hp": 2, "seconds": 100})
    balance = GameBalance.model_validate({"base_seconds": 40, "seconds_per_point": 30})
    assert allocation.model_dump() == {
        "hp": 2,
        "mp": 0,
        "attack": 0,
        "defense": 0,
        "specialAttack": 0,
        "specialDefense": 0,
        "speed": 0,
    }
    assert allocation.stats(balance)["answerSeconds"] == 40


@pytest.mark.parametrize(
    "settings",
    [
        {"damage_effect_threshold_1": 50},
        {"damage_effect_threshold_3": 50},
        {"damage_effect_threshold_1": -100},
        {"damage_effect_label_1": "   "},
        {"damage_effect_label_4": ""},
    ],
)
def test_invalid_damage_effect_settings_are_rejected(settings: dict) -> None:
    """4段階が逆転・消失する区切りや、空の文言を拒否する."""
    with pytest.raises(ValidationError):
        GameBalance.model_validate(settings)


def test_damage_effect_settings_are_loaded_from_old_settings_and_roundtrip() -> None:
    """旧設定は4演出の既定値を使い、編集した文言と区切りは保存できる."""
    old = GameBalance.model_validate({"damage_variance_percent": 10})
    assert old.damage_effect_threshold_3 == 75
    changed = old.model_copy(
        update={"damage_effect_label_4": "大当たり", "damage_effect_threshold_3": 80},
    )
    assert GameBalance.model_validate_json(changed.model_dump_json()) == changed
