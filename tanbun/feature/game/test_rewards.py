"""報酬表の段階・抽選・JSON交換をDBなしで検証する."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from . import rewards
from .balance import GameBalance
from .catalog import GameCatalog, default_catalog
from .rewards import (
    GoldSettings,
    RewardEntry,
    RewardSettings,
    RewardTable,
    roll_gold,
    roll_reward,
)
from .settings import BattleSettings
from .settings_files import SettingsSnapshot, merge_settings


@pytest.fixture(autouse=True)
def setup():
    """Override the global destructive DB fixture for pure calculations."""


@pytest.mark.parametrize(("power", "gold"), [(0, 50), (100, 60), (400, 70)])
def test_power_gold(power, gold):
    """Powerの平方根に応じてGoldだけを増やす."""
    assert roll_gold(GoldSettings(), power, 1, "chest", variation=0) == gold


@pytest.mark.parametrize("power", [0, 100, 3000])
@pytest.mark.parametrize("achievement", [0, 1, 5, 20])
def test_chest_minimum_exceeds_enemy_maximum_with_defaults(power, achievement):
    """標準設定では、バラツキの両端を比べても宝箱を多くする."""
    settings = GoldSettings()
    assert roll_gold(settings, power, achievement, "chest", variation=-1) > roll_gold(
        settings,
        power,
        achievement,
        "enemy",
        variation=1,
    )


def test_common_gold_achievement_variance_formula_and_cap():
    """成長とバラツキは同じ式、用途の倍率だけが異なる."""
    settings = GoldSettings()
    assert roll_gold(settings, 100, 5, "enemy", variation=0) == 21
    assert roll_gold(settings, 100, 5, "enemy", variation=-1) == 19
    assert roll_gold(settings, 100, 5, "enemy", variation=1) == 23
    assert (
        roll_gold(GoldSettings(power_formula="log"), 0, 0, "enemy", variation=0) == 10
    )
    assert (
        roll_gold(
            GoldSettings(base=100000, power_coefficient=100000),
            10**12,
            20,
            "chest",
            variation=1,
        )
        == 1000000
    )


@pytest.mark.parametrize(
    ("level", "item"),
    [(0, "a"), (4, "a"), (5, "b"), (9, "b"), (10, "c"), (999, "c")],
)
def test_achievement_tier(level, item):
    """達成度の境界で抽選段階を切り替える."""
    table = RewardTable(
        id="chest",
        name="宝箱",
        entries=[
            RewardEntry(item_id=name, tier=tier) for tier, name in enumerate("abc", 1)
        ],
    )
    assert list(roll_reward(table, level, dict.fromkeys("abc", "装備")).items) == [
        item,
    ]


def test_fallback_never_grants_higher_tier_and_ignores_unavailable_items():
    """未設定の段階では下位だけを使う."""
    table = RewardTable(
        id="chest",
        name="宝箱",
        tier_interval=3,
        entries=[
            RewardEntry(item_id="a", tier=1),
            RewardEntry(item_id="b", tier=3),
        ],
    )
    assert list(roll_reward(table, 3, {"a": "剣", "b": "杖"}).items) == ["a"]
    assert list(roll_reward(table, 6, {"a": "剣"}).items) == ["a"]
    assert not roll_reward(table, 0, {"b": "杖"}).items


def test_weight_boundaries_multiple_draws_and_names(monkeypatch):
    """重みの境界と複数回抽選の合算を検証する."""
    table = RewardTable(
        id="chest",
        name="宝箱",
        draws=3,
        entries=[
            RewardEntry(item_id="a", weight=2, quantity=2),
            RewardEntry(item_id="b", weight=3, quantity=1),
        ],
    )
    draws = iter([0, 1, 4])
    monkeypatch.setattr(rewards, "randbelow", lambda _: next(draws))
    result = roll_reward(table, 0, {"a": "剣", "b": "杖"})
    assert result.items["a"].quantity == 4
    assert result.items["b"].name == "杖"
    assert result.items["b"].quantity == 1


def test_chance_zero_partial_and_full(monkeypatch):
    """アイテムの付与率はGold計算と独立して適用する."""
    table = RewardTable(
        id="chest",
        name="宝箱",
        draws=2,
        item_chance=50,
        entries=[RewardEntry(item_id="a")],
    )
    draws = iter([49, 0, 50])
    monkeypatch.setattr(rewards, "randbelow", lambda _: next(draws))
    assert roll_reward(table, 0, {"a": "剣"}).items["a"].quantity == 1
    table.item_chance = 0
    assert not roll_reward(table, 0, {"a": "剣"}).items


@pytest.mark.parametrize(
    "patch",
    [
        {"tier_interval": 0},
        {"draws": 11},
        {"item_chance": 101},
    ],
)
def test_invalid_settings(patch):
    """計算不能な設定を保存させない."""
    with pytest.raises(ValidationError):
        RewardTable(id="chest", name="宝箱", **patch)


@pytest.mark.parametrize(
    "patch",
    [
        {"power_reference": 0},
        {"power_coefficient": float("nan")},
        {"variance_percent": 51},
    ],
)
def test_invalid_gold_settings(patch):
    """Goldの式が無限や負の確率にならないよう検証する."""
    with pytest.raises(ValidationError):
        GoldSettings(**patch)


def test_duplicate_and_missing_references_are_rejected():
    """重複候補と存在しない参照を拒否する."""
    with pytest.raises(ValidationError):
        RewardTable(id="chest", name="宝箱", entries=[RewardEntry(item_id="a")] * 2)
    with pytest.raises(ValidationError):
        RewardSettings(chest_table_id="missing")
    with pytest.raises(ValidationError):
        GameCatalog(
            rewards=RewardSettings(
                tables=[
                    RewardTable(
                        id="chest",
                        name="宝箱",
                        entries=[RewardEntry(item_id="missing")],
                    ),
                ],
            ),
        )


def test_unified_settings_round_trip_preserves_other_catalog_and_balance():
    """統一JSONで他の設定を残しながら報酬だけを交換する."""
    current = SettingsSnapshot(
        catalog=default_catalog(),
        balance=GameBalance(),
        battle=BattleSettings(),
    )
    definition = RewardSettings(
        gold=GoldSettings(base=17, chest_multiplier=7),
        chest_table_id="chest",
        tables=[
            RewardTable(
                id="chest",
                name="宝箱",
                entries=[RewardEntry(item_id="bronze_sword")],
            ),
        ],
    )
    patch = {
        "format": "tanbunism.game-settings",
        "version": 1,
        "catalog": {"rewards": definition.model_dump(mode="json")},
        "balance": {"chest_gold": 50},
    }
    merged = merge_settings(current, patch)
    assert merged.catalog.rewards == definition
    assert merged.catalog.skills == current.catalog.skills
    assert merged.balance.base_hp == current.balance.base_hp
    assert merged.balance.chest_gold == 50
    assert merge_settings(merged, merged.document()).catalog.rewards == definition
