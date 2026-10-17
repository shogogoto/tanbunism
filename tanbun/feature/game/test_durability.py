"""耐久・状態変化を実戦と模擬戦で共通の精算に接続する."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .balance import GameBalance
from .catalog import AilmentSkill, ItemDefinition, default_catalog
from .combat import TurnRequest, apply_turn_result
from .effects import add_effect, curse_damage, poison_damage
from .inventory import Economy, consume_use, equipped_stats, wear_weapon
from .test_gambits import battle_state


def test_weapon_breaks_on_last_use_and_does_not_auto_equip_spare():
    """壊れた一個のみ失い、予備を勝手に装備しない."""
    economy = Economy(inventory={"sword": 2}, weapon="sword", equipment_attack=8)
    assert not wear_weapon(economy, 29)
    assert economy.remaining_uses == {"sword": 1}
    assert wear_weapon(economy, 2)
    assert economy.inventory["sword"] == 1
    assert economy.weapon is None
    assert economy.weapon_attack == 0
    assert economy.remaining_uses == {}


def test_durability_belongs_to_item_not_equipped_slot():
    """別の武器への付け替えでも残回数が変わらない."""
    economy = Economy(inventory={"a": 1, "b": 1}, weapon="a")
    wear_weapon(economy, 10)
    economy.weapon = "b"
    wear_weapon(economy, 2)
    economy.weapon = "a"
    wear_weapon(economy, 1)
    assert economy.remaining_uses == {"a": 19, "b": 28}


def test_consumable_uses_are_per_copy_and_potions_default_to_once():
    """使用回数が一回の品は個数を減らし、複数回品は残回数から消費する."""
    economy = Economy(inventory={"potion": 2, "wand": 2})
    assert consume_use(economy, "potion", 1)
    assert economy.inventory["potion"] == 1
    assert not consume_use(economy, "wand", 2)
    assert economy.inventory["wand"] == 2
    assert consume_use(economy, "wand", 2)
    assert economy.inventory["wand"] == 1
    assert "wand" not in economy.remaining_uses


def test_old_catalog_migrates_durability_without_resetting_explicit_values():
    """保存済みの武器は三十回、消耗品は一回で移行する."""
    assert ItemDefinition(id="s", name="剣", kind="weapon", skill_id="s").max_uses == 30
    assert ItemDefinition(id="p", name="薬", skill_id="p").max_uses == 1
    assert (
        ItemDefinition(
            id="s",
            name="剣",
            kind="weapon",
            skill_id="s",
            max_uses=5,
        ).max_uses
        == 5
    )
    with pytest.raises(ValidationError):
        ItemDefinition(id="s", name="剣", kind="weapon", skill_id="s", max_uses=0)


def test_only_correct_normal_attacks_wear_weapon_and_break_updates_stats():
    """正解一回で破損、未回答では消耗せず、元スナップショットも変えない."""
    state = battle_state()
    state.save.economy = Economy(
        inventory={"s": 1},
        weapon="s",
        equipment_attack=8,
        remaining_uses={"s": 1},
    )
    stats = equipped_stats(state.save.allocation, GameBalance(), state.save.economy)
    body = TurnRequest(battle_id="battle", turn=0)
    wrong = apply_turn_result(state, body, {}, 0, stats)
    assert wrong.save.economy.remaining_uses["s"] == 1
    correct = apply_turn_result(state, body, {"a": True}, 0, stats)
    assert correct.save.economy.weapon is None
    assert correct.save.run.attack == stats["attack"] - 8
    assert "武器が壊れた" in correct.save.battleFeedback
    assert state.save.economy.weapon == "s"


def test_curse_and_poison_tick_separately_and_expire():
    """呪いを毒としてHPに適用せず、期限切れも除く."""
    state = battle_state()
    marker = state.save.battle
    poison = AilmentSkill(id="poison", name="毒", amount=2, duration_turns=1)
    curse = AilmentSkill(
        id="curse",
        name="呪い",
        ailment="curse",
        amount=30,
        amount_mode="percent",
        duration_turns=1,
    )
    add_effect(marker, poison, "player")
    add_effect(marker, curse, "player")
    assert curse_damage(marker, {"player": 10}) == {}
    marker.turn = 1
    assert curse_damage(marker, {"player": 10}) == {"player": 3}
    assert poison_damage(marker, {"player": 35}) == {"player": 2}
    updated = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=1),
        {},
        1,
        state.save.allocation.stats(GameBalance()),
    )
    assert updated.save.run.hp == 7
    assert updated.save.run.mp == 7
    assert curse_damage(updated.save.battle, {"player": 10}) == {}


def test_enemy_on_hit_ailment_is_next_turn_only_and_clears_after_battle():
    """命中時に付与し、正解時には付与しない。撤退で持ち越さない."""
    state = battle_state()
    curse = AilmentSkill(id="curse", name="呪い", ailment="curse", amount=2)
    body = TurnRequest(battle_id="battle", turn=0)
    stats = state.save.allocation.stats(GameBalance())
    correct = apply_turn_result(
        state,
        body,
        {"a": True},
        0,
        stats,
        on_hit_skills={"a": curse},
    )
    assert not correct.save.battle.effects
    wrong = apply_turn_result(state, body, {}, 0, stats, on_hit_skills={"a": curse})
    assert wrong.save.run.mp == 10
    assert curse_damage(wrong.save.battle, {"player": 10}) == {"player": 2}
    retreated = apply_turn_result(
        wrong,
        body.model_copy(update={"turn": 1, "retreat": True}),
        {},
        0,
        stats,
    )
    assert retreated.save.run.mp == 8
    assert retreated.save.battle is None


def test_enemy_on_hit_references_reject_missing_or_attack_skills():
    """命中時効果を削除したり攻撃スキルに変更すると保存を拒否する."""
    for reference in ("missing", "fire"):
        catalog = default_catalog().model_dump()
        catalog["enemies"][0]["on_hit_skill_id"] = reference
        with pytest.raises(ValidationError, match="命中時"):
            type(default_catalog()).model_validate(catalog)
