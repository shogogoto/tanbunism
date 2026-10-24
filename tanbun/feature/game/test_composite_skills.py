"""一問・一回分の消費で複数の効果を解決するスキル."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .balance import GameBalance
from .catalog import GameCatalog
from .end_actions import begin_action, finish_action
from .gambits import BattleAction, can_execute
from .scheduled_combat import resolve_scheduled
from .skill_files import SkillFile
from .skill_outcomes import skill_effects
from .test_scheduled_combat import answer, encounter


def composite_catalog():
    """攻撃・HP回復・毒付与を組み合わせる."""
    return GameCatalog.model_validate({
        "skill_templates": [
            {
                "id": "hit",
                "name": "打撃",
                "effect": "attack",
                "multiplier": 1,
                "on_wrong": {"type": "counter"},
            },
            {
                "id": "cure",
                "name": "回復",
                "effect": "heal_hp",
                "amount": 10,
                "on_wrong": {"type": "reduced", "amount": 5},
            },
            {
                "id": "venom",
                "name": "毒",
                "effect": "ailment",
                "ailment": "poison",
                "amount": 2,
            },
        ],
        "skills": [
            {
                "id": "drain",
                "name": "毒の吸収攻撃",
                "template_ids": ["hit", "cure", "venom"],
                "mp_cost": 3,
            },
        ],
    })


def test_references_round_trip_without_copying_effect_values_or_mp_to_templates():
    """保存は参照だけ、JSON交換後も同じ効果になる."""
    catalog = composite_catalog()
    stored = catalog.stored_document()
    assert stored["skills"][0]["template_ids"] == ["hit", "cure", "venom"]
    assert stored["skills"][0]["mp_cost"] == 3
    assert "amount" not in stored["skills"][0]
    assert "effects" not in stored["skills"][0]
    assert all("mp_cost" not in template for template in stored["skill_templates"])
    assert GameCatalog.model_validate(stored) == catalog
    exported = SkillFile.model_validate({
        "format": "tanbunism.skills",
        "version": 5,
        "skills": stored["skills"],
        "skill_templates": stored["skill_templates"],
    })
    assert exported.skills[0].effects == catalog.skills[0].effects
    assert [effect.id for effect in skill_effects(catalog.skills[0])] == [
        "hit",
        "cure",
        "venom",
    ]


@pytest.mark.parametrize("correct", [True, False])
def test_one_answer_applies_all_effects_and_spends_mp_once(correct):
    """正誤を全効果で共有し、親のMPだけを消費する."""
    state, context = encounter()
    skill = composite_catalog().skills[0]
    state.save.run.hp = 15
    state.save.run.mp = 10
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.schedule.quiz_enemy_id = "e"
    marker.gambitAction = BattleAction(skill=skill, name=skill.name)
    body, results = answer(state, correct=correct)
    reply = resolve_scheduled(
        state,
        body,
        results,
        context,
        roll_player=lambda *_: 8,
        roll_enemy=lambda *_: 4,
    )
    updated = reply["state"].save
    assert updated.run.mp == 7
    assert updated.run.hp == (25 if correct else 16)
    assert reply["outgoing_damage"] == ({"e": 8} if correct else {})
    assert len(updated.battle.effects) == (1 if correct else 0)
    assert state.save.run.mp == 10


def test_multiple_counter_effects_counter_only_once_and_damage_is_capped():
    """複数反撃で返り討ちが増えず、合計ダメージは残HP以下になる."""
    state, context = encounter()
    document = composite_catalog().stored_document()
    document["skill_templates"].append({
        "id": "hit_two",
        "name": "二撃目",
        "effect": "attack",
        "on_wrong": {"type": "counter"},
    })
    document["skills"][0]["template_ids"] = ["hit", "hit_two"]
    skill = GameCatalog.model_validate(document).skills[0]
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.schedule.quiz_enemy_id = "e"
    marker.gambitAction = BattleAction(skill=skill, name=skill.name)
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(state, body, results, context, roll_enemy=lambda *_: 4)
    assert reply["damage"] == 4
    body, results = answer(state, correct=True)
    reply = resolve_scheduled(state, body, results, context, roll_player=lambda *_: 70)
    assert reply["outgoing_damage"] == {"e": 100}


@pytest.mark.parametrize("item", [False, True])
def test_specialty_and_item_paths_share_all_effects_and_consume_only_once(item):
    """専門分野とアイテムの経路でも、効果数で消費回数を増やさない."""
    state, _ = encounter()
    skill = composite_catalog().skills[0]
    state.save.run.hp = 15
    state.save.run.mp = state.save.run.maxMp = 10
    state.save.economy.inventory["potion"] = 2
    marker = state.save.battle
    begin_action(
        state.save,
        BattleAction(skill=skill, name=skill.name, item_id="potion" if item else None),
        skill,
        [],
        {"e": 100, "e2": 100},
    )
    outgoing = finish_action(state)
    assert state.save.run.mp == (10 if item else 7)
    assert state.save.economy.inventory["potion"] == (1 if item else 2)
    assert state.save.run.hp == 25
    assert len(marker.effects) == 1
    assert outgoing["e"] > 0
    assert marker.endAction is None


def test_full_hp_does_not_skip_a_skill_with_other_useful_effects():
    """最初の回復が不要でも攻撃効果を発動できる."""
    state, _ = encounter()
    state.save.run.hp = state.save.run.maxHp
    state.save.run.mp = 10
    document = composite_catalog().stored_document()
    document["skills"][0]["template_ids"] = ["cure", "hit"]
    assert can_execute(
        state.save,
        GameCatalog.model_validate(document).skills[0],
        uses_item=False,
    )


def test_enemy_composition_recovers_self_and_harms_player():
    """敵も複合定義を使い、自分を回復しプレイヤーに状態を付与する."""
    state, context = encounter()
    context.balance = GameBalance(base_hp=100)
    state.save.run.hp = state.save.run.maxHp = 100
    enemy = context.enemies[0]
    enemy.mp = 10
    enemy.attack_skill = composite_catalog().skills[0]
    schedule = state.save.battle.schedule
    schedule.actor_id = schedule.quiz_enemy_id = "e"
    schedule.enemy_mp["e"] = 10
    schedule.enemy_max_mp["e"] = 10
    schedule.enemy_hp["e"] = 50
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(state, body, results, context, roll_enemy=lambda *_: 4)
    assert reply["damage"] == 4
    assert reply["enemy_mp"]["e"] == 7
    assert reply["enemy_hp"]["e"] == 60
    assert reply["state"].save.battle.effects[0].target_id == "player"


@pytest.mark.parametrize("references", [["hit", "hit"], ["missing"]])
def test_duplicate_and_missing_effects_are_rejected(references):
    """重複・参照切れを保存前に拒否する."""
    document = composite_catalog().stored_document()
    document["skills"][0]["template_ids"] = references
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(document)
