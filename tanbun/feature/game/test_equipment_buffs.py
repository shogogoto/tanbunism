"""装備は専用効果ではなく共通の状態バフを利用する."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test

from .balance import Allocation, GameBalance
from .catalog import GameCatalog, default_catalog, update_game_catalog
from .combat import TurnRequest, apply_turn_result
from .economy import EconomyAction, catalog_products, change_economy
from .inventory import Economy, EquipmentBuff, equipped_stats
from .test_economy import seed_user
from .test_gambits import battle_state


def buff_catalog():
    """防御・特攻・最大HPのバフを、一つの装備用スキルで共有する."""
    data = default_catalog().model_dump()
    skill = next(s for s in data["skills"] if s["id"] == "bronze_power")
    skill.update(
        stat="defense",
        amount=4,
        effects=[
            {"effect": "buff", "stat": "defense", "amount": 4},
            {
                "effect": "buff",
                "stat": "specialAttack",
                "amount": 50,
                "amount_mode": "percent",
            },
            {
                "effect": "buff",
                "stat": "maxHp",
                "amount": 100,
                "amount_mode": "percent",
            },
        ],
    )
    return GameCatalog.model_validate(data)


def test_weapon_reuses_only_buff_components_and_keeps_legacy_products():
    """装備可否はアイテム側で決め、攻撃や回復の混在は拒否する."""
    catalog = buff_catalog()
    product = next(p for p in catalog_products(catalog) if p.id == "bronze_sword")
    assert product.kind == "weapon"
    assert [b.stat for b in product.equipment_buffs] == [
        "defense",
        "specialAttack",
        "maxHp",
    ]
    assert product.amount == 4
    for effect in ["attack", "heal_hp", "debuff", "ailment"]:
        raw = catalog.model_dump()
        raw["skills"] = [
            {"id": s["id"], "name": s["name"], "effect": effect}
            if s["id"] == "bronze_power"
            else s
            for s in raw["skills"]
        ]
        with pytest.raises(ValidationError):
            GameCatalog.model_validate(raw)


def test_equipment_percentages_use_base_stats_and_do_not_mutate_snapshots():
    """同じ能力への割合は基礎値を基準に足し、読み込みでも保持する."""
    economy = Economy(
        weapon="armor",
        inventory={"armor": 1},
        equipment_buffs=[
            EquipmentBuff(stat="defense", amount=50, amount_mode="percent"),
            EquipmentBuff(stat="defense", amount=50, amount_mode="percent"),
            EquipmentBuff(stat="defense", amount=2),
        ],
    )
    before = economy.model_dump_json()
    assert (
        equipped_stats(Allocation(), GameBalance(base_defense=4), economy)["defense"]
        == 10
    )
    assert economy.model_dump_json() == before
    restored = Economy.model_validate_json(before)
    assert (
        equipped_stats(Allocation(), GameBalance(base_defense=4), restored)["defense"]
        == 10
    )
    restored.inventory.clear()
    assert (
        equipped_stats(Allocation(), GameBalance(base_defense=4), restored)["defense"]
        == 4
    )


@mark_async_test()
async def test_equip_and_unequip_apply_all_buffs_and_preserve_hp_ratio():
    """装備中だけ有効。最大HPバフは残HPも同じ比率で伸縮する."""
    await update_game_catalog(buff_catalog())
    user = await seed_user()
    await change_economy(
        user.uid,
        EconomyAction(revision=0, product_id="bronze_sword"),
        "buy",
    )
    equipped = await change_economy(
        user.uid,
        EconomyAction(revision=1, product_id="bronze_sword"),
        "equip",
    )
    run = equipped.save.run
    assert (run.attack, run.defense, run.specialAttack) == (10, 5, 15)
    assert (run.hp, run.maxHp) == (20, 70)
    assert equipped.save.economy.equipment_buffs
    unequipped = await change_economy(
        user.uid,
        EconomyAction(revision=2, product_id="bronze_sword"),
        "equip",
    )
    run = unequipped.save.run
    assert (run.attack, run.defense, run.specialAttack) == (10, 1, 10)
    assert (run.hp, run.maxHp) == (10, 35)
    assert unequipped.save.economy.equipment_buffs is None


def test_breaking_equipment_removes_every_buff_in_the_same_action():
    """破損時に攻撃力以外のバフを残さず、入力の能力辞書も書き換えない."""
    state = battle_state()
    state.save.economy = Economy(
        weapon="armor",
        inventory={"armor": 1},
        remaining_uses={"armor": 1},
        equipment_buffs=[
            EquipmentBuff(stat="defense", amount=9),
            EquipmentBuff(stat="maxHp", amount=100, amount_mode="percent"),
        ],
    )
    balance = GameBalance(base_hp=40, base_defense=2)
    stats = equipped_stats(state.save.allocation, balance, state.save.economy)
    base = state.save.allocation.stats(balance)
    state.save.run.maxHp, state.save.run.hp = 80, 40
    updated = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0),
        {"a": False},
        10,
        stats,
        base_stats=base,
    )
    assert (updated.save.run.defense, updated.save.run.maxHp, updated.save.run.hp) == (
        2,
        40,
        15,
    )
    assert updated.save.economy.weapon is None
    assert updated.save.economy.equipment_buffs is None
    assert stats["defense"] == 11
    assert state.save.economy.weapon == "armor"
