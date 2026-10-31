"""七つの装備枠・合算効果・個別の破損を検証する."""
# ruff: noqa: PLR2004

import pytest

from tanbun.conftest import mark_async_test

from .balance import Allocation, GameBalance
from .catalog import default_catalog, update_game_catalog
from .combat import TurnRequest, apply_turn_result
from .economy import EconomyAction, change_economy
from .inventory import (
    Economy,
    EquipmentBuff,
    Product,
    equip_item,
    equipped_stats,
    wear_weapon,
)
from .test_economy import seed_user
from .test_gambits import battle_state


def gear(identity="sword", part="arm", stat="attack", amount=3):
    """名前・部位・効果が独立した装備品."""
    return Product(
        id=identity,
        name=identity,
        price=10,
        kind="weapon",
        category="weapon" if part == "arm" else "armor",
        equipment_part=part,
        amount=amount,
        max_uses=30,
        equipment_buffs=[EquipmentBuff(stat=stat, amount=amount)],
    )


def test_equipment_slots_combine_and_move_without_resetting_durability():
    """効果を合算し、移動しても残回数と所持数を維持する."""
    economy = Economy(inventory={"sword": 2, "armor": 1}, remaining_uses={"sword": 8})
    equip_item(economy, gear(), "arm1")
    equip_item(economy, gear("armor", "body", "defense", 4), "body")
    stats = equipped_stats(Allocation(), GameBalance(), economy)
    assert stats["attack"] == GameBalance().base_attack + 3
    assert stats["defense"] == GameBalance().base_defense + 4
    equip_item(economy, gear(), "arm2")
    assert set(economy.equipment) == {"arm2", "body"}
    assert economy.remaining_uses["sword"] == 8
    assert (
        Economy.model_validate_json(economy.model_dump_json()).equipment
        == economy.equipment
    )
    with pytest.raises(ValueError, match="部位"):
        equip_item(economy, gear(), "head")
    assert set(economy.equipment) == {"arm2", "body"}


def test_durability_is_separate_for_attacking_and_taking_damage():
    """武器と防具が独立して消耗し、予備を自動装備しない."""
    economy = Economy(
        inventory={"sword": 1, "armor": 2},
        remaining_uses={"sword": 1, "armor": 1},
    )
    equip_item(economy, gear(), "arm1")
    equip_item(economy, gear("armor", "body", "defense", 4), "body")
    assert wear_weapon(economy, 1)
    assert set(economy.equipment) == {"body"}
    assert economy.remaining_uses["armor"] == 1
    assert (
        equipped_stats(Allocation(), GameBalance(), economy)["defense"]
        == GameBalance().base_defense + 4
    )
    assert wear_weapon(economy, 1, defensive=True)
    assert economy.equipment == {}
    assert economy.inventory["armor"] == 1


def test_breaking_one_piece_keeps_other_equipment_stats_in_combat():
    """武器が壊れても残っている防具の補正は継続する."""
    state = battle_state()
    economy = Economy(inventory={"sword": 1, "armor": 1}, remaining_uses={"sword": 1})
    equip_item(economy, gear(), "arm1")
    equip_item(economy, gear("armor", "body", "defense", 4), "body")
    state.save.economy = economy
    balance = GameBalance()
    result = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0),
        {"a": True},
        0,
        equipped_stats(state.save.allocation, balance, economy),
        base_stats=state.save.allocation.stats(balance),
    )
    assert result.save.run.attack == balance.base_attack
    assert result.save.run.defense == balance.base_defense + 4
    assert result.save.economy.equipment["body"].item_id == "armor"
    assert "装備が壊れた" in result.save.battleFeedback


@mark_async_test()
async def test_slot_equip_api_preserves_old_gear_and_revision():
    """新しい枠への装備で、旧武器を保持して移行する."""
    catalog = default_catalog()
    next(item for item in catalog.shop if item.item_id == "steel_sword").price = 50
    await update_game_catalog(catalog)
    user = await seed_user()
    state = await change_economy(
        user.uid,
        EconomyAction(revision=0, product_id="bronze_sword"),
        "buy",
    )
    state = await change_economy(
        user.uid,
        EconomyAction(revision=state.revision, product_id="bronze_sword"),
        "equip",
    )
    state = await change_economy(
        user.uid,
        EconomyAction(revision=state.revision, product_id="steel_sword"),
        "buy",
    )
    state = await change_economy(
        user.uid,
        EconomyAction(revision=state.revision, product_id="steel_sword", slot="arm2"),
        "equip",
    )
    assert {
        slot: item.item_id for slot, item in state.save.economy.equipment.items()
    } == {"arm1": "bronze_sword", "arm2": "steel_sword"}
    state = await change_economy(
        user.uid,
        EconomyAction(revision=state.revision, product_id="steel_sword", slot="arm2"),
        "equip",
    )
    assert set(state.save.economy.equipment) == {"arm1"}
