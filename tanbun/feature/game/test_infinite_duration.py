"""持続数の無限と装備の使用回数を独立に検証する."""
# ruff: noqa: PLR2004

import pytest

from .balance import GameBalance
from .catalog import AilmentSkill, BuffSkill, GameCatalog, parse_skill
from .combat import TurnRequest, apply_turn_result
from .effects import add_effect, effect_active, modified_stat, poison_damage
from .end_actions import apply_end_counter
from .inventory import Economy, EquipmentBuff, equipped_stats
from .skill_files import SkillFile
from .skill_outcomes import answer_effect
from .test_gambits import battle_state


@pytest.mark.parametrize("kind", ["buff", "debuff", "ailment"])
def test_infinite_duration_and_wrong_override_survive_json(kind):
    """正誤それぞれの無限を、巨大な整数にせず保存する."""
    raw = {
        "id": "state",
        "name": "状態",
        "effect": kind,
        "duration_turns": "infinite",
        "on_wrong": {
            "type": "reduced",
            **({"apply": True} if kind == "ailment" else {"amount": 1}),
            "duration_turns": "infinite",
        },
    }
    skill = parse_skill(raw)
    assert answer_effect(skill, correct=False).duration_turns == "infinite"
    catalog = GameCatalog.model_validate({"skills": [raw]})
    assert GameCatalog.model_validate(catalog.stored_document()) == catalog
    assert (
        SkillFile
        .model_validate({"format": "tanbunism.skills", "version": 6, "skills": [raw]})
        .skills[0]
        .duration_turns
        == "infinite"
    )


def test_unbounded_effects_start_at_next_action_and_do_not_expire_or_overstack():
    """有限の効果は切れ、無限の状態は開始条件と重複上限を守る."""
    marker = battle_state().save.battle
    infinite = BuffSkill(id="buff", name="強化", amount=3, duration_turns="infinite")
    finite = BuffSkill(id="finite", name="一時", amount=2, duration_turns=1)
    add_effect(marker, infinite, "player")
    add_effect(marker, finite, "player")
    assert modified_stat(10, marker, "player", "attack") == 10
    marker.turn = 1
    assert modified_stat(10, marker, "player", "attack") == 15
    marker.turn = 10000
    assert modified_stat(10, marker, "player", "attack") == 13
    assert effect_active(marker.effects[0], marker.turn)
    add_effect(marker, infinite, "player")
    assert len(marker.effects) == 1
    assert marker.effects[0].expires_turn is None
    marker.turn += 1
    assert modified_stat(10, marker, "player", "attack") == 13
    poison = AilmentSkill(id="poison", name="毒", amount=1, duration_turns="infinite")
    add_effect(marker, poison, "player")
    marker.turn += 10000
    assert poison_damage(marker, {"player": 10}) == {"player": 1}


def test_shared_duration_set_can_mix_finite_and_infinite():
    """共有する持続数の候補でも無限を扱える."""
    catalog = GameCatalog.model_validate({
        "variant_sets": [
            {
                "id": "duration",
                "name": "持続数",
                "field": "duration_turns",
                "values": [
                    {"id": "forever", "name": "装備", "value": "infinite"},
                    {"id": "short", "name": "一時", "value": 3},
                ],
            },
        ],
        "skill_templates": [
            {
                "id": "base",
                "name": "強化",
                "effect": "buff",
                "variant_set_ids": ["duration"],
            },
            {
                "id": "gear",
                "name": "装備",
                "base_template_id": "base",
                "variant_selections": [{"set_id": "duration", "value_id": "forever"}],
            },
        ],
        "skills": [{"id": "buff", "name": "強化", "template_ids": ["gear"]}],
    })
    assert catalog.skills[0].duration_turns == "infinite"
    assert GameCatalog.model_validate(catalog.stored_document()) == catalog


def test_infinite_battle_effect_still_clears_when_battle_ends():
    """無限は戦闘外への持ち越しを意味しない."""
    state = battle_state()
    add_effect(
        state.save.battle,
        BuffSkill(id="forever", name="強化", amount=3, duration_turns="infinite"),
        "player",
    )
    updated = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0, retreat=True),
        {},
        0,
        state.save.allocation.stats(GameBalance()),
    )
    assert updated.save.battle is None
    assert state.save.battle.effects[0].expires_turn is None


def armor_state(uses):
    """攻撃補正を持たない防具と、装備で倍になったHP."""
    state = battle_state()
    state.save.economy = Economy(
        weapon="armor",
        inventory={"armor": 1},
        remaining_uses={"armor": uses},
        equipment_buffs=[
            EquipmentBuff(stat="defense", amount=9),
            EquipmentBuff(stat="maxHp", amount=100, amount_mode="percent"),
        ],
    )
    balance = GameBalance(base_hp=40, base_defense=2)
    base = state.save.allocation.stats(balance)
    stats = equipped_stats(state.save.allocation, balance, state.save.economy)
    state.save.run.maxHp, state.save.run.hp = 80, 40
    return state, base, stats


def test_armor_wears_on_damage_not_correct_answers_and_breaks_after_final_protection():
    """誤答の反撃で消耗。最後の被ダメージ適用後にHPも同率で縮小する."""
    state, base, stats = armor_state(1)
    body = TurnRequest(battle_id="battle", turn=0)
    unchanged = apply_turn_result(state, body, {"a": True}, 0, stats, base_stats=base)
    assert unchanged.save.economy.remaining_uses["armor"] == 1
    hit = apply_turn_result(state, body, {"a": False}, 10, stats, base_stats=base)
    assert (hit.save.run.hp, hit.save.run.maxHp, hit.save.run.defense) == (15, 40, 2)
    assert hit.save.economy.weapon is None
    assert hit.save.economy.equipment_buffs is None
    assert "防具が壊れた" in hit.save.battleFeedback
    assert state.save.economy.remaining_uses["armor"] == 1


def test_specialty_counter_also_wears_armor():
    """追加クイズの反撃も同じ防具消耗を通す."""
    state, base, stats = armor_state(1)
    state.save.battleFeedback = "不正解"
    state.save.endActionReceipt = {}
    skill = parse_skill({
        "id": "counter",
        "name": "反撃型",
        "effect": "attack",
        "on_wrong": {"type": "counter"},
    })
    apply_end_counter(
        state,
        {
            "quiz_ids": ["q"],
            "correct": 0,
            "counter_damage": 10,
            "equipment_stats": stats,
            "base_stats": base,
        },
        skill,
    )
    assert state.save.economy.weapon is None
    assert state.save.run.hp == 15


def test_correct_non_attack_does_not_consume_weapon():
    """行動の正答ではなく、実際に攻撃した回数を渡せる."""
    state = battle_state()
    state.save.economy = Economy(
        weapon="sword",
        inventory={"sword": 1},
        remaining_uses={"sword": 1},
        equipment_attack=3,
    )
    stats = equipped_stats(state.save.allocation, GameBalance(), state.save.economy)
    result = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0),
        {"a": True},
        0,
        stats,
        equipment_hits=0,
    )
    assert result.save.economy.remaining_uses["sword"] == 1
