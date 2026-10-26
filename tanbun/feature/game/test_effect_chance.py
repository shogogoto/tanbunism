"""スキルごとの効果発動率と、対象ごとに固定される付与抽選。."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .catalog import GameCatalog
from .effect_chance import effect_activates
from .effects import poison_damage
from .end_actions import begin_action, finish_action
from .gambits import BattleAction, apply_action
from .scheduled_combat import resolve_scheduled
from .skill_files import SkillFile
from .skill_outcomes import skill_effects
from .state import BattleMarker
from .test_composite_skills import composite_catalog
from .test_scheduled_combat import answer, encounter


def chance_catalog(chance):
    """確実な打撃・回復に、確率で付与する毒を追加する。."""
    raw = composite_catalog().stored_document()
    raw["skills"][0]["effect_chances"] = {"venom": chance}
    return GameCatalog.model_validate(raw)


@pytest.mark.parametrize("chance", [0, 100])
@pytest.mark.parametrize("correct", [True, False])
def test_player_composite_preserves_damage_cost_and_answer_policy(chance, correct):
    """追加状態だけを抽選し、MP消費や通常ダメージを変えない。."""
    state, context = encounter()
    state.save.run.mp = 10
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    skill = chance_catalog(chance).skills[0]
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
    save = reply["state"].save
    assert save.run.mp == 7
    assert reply["outgoing_damage"] == ({"e": 8} if correct else {})
    assert len(save.battle.effects) == int(correct and chance == 100)


@pytest.mark.parametrize("chance", [0, 100])
def test_enemy_actions_use_same_probability_policy(chance):
    """敵の付与にも正誤を反転した後の発動率を適用する。."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "e"
    marker.schedule.quiz_enemy_id = "e"
    context.enemies[0].attack_skill = chance_catalog(chance).skills[0]
    marker.schedule.enemy_mp["e"] = 10
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(state, body, results, context, roll_enemy=lambda *_: 1)
    assert len(reply["state"].save.battle.effects) == int(chance == 100)


@pytest.mark.parametrize("chance", [0, 100])
def test_end_action_applies_per_effect_rate(chance):
    """専門分野の追加行動も同じ発動率を使う。."""
    state, _ = encounter()
    state.save.run.mp = state.save.run.maxMp = 10
    skill = chance_catalog(chance).skills[0]
    marker = state.save.battle
    selected = BattleAction(skill=skill, name=skill.name)
    begin_action(state.save, selected, skill, [], marker.schedule.enemy_hp)
    assert finish_action(state)["e"] > 0
    assert len(state.save.battle.effects) == int(chance == 100)
    assert state.save.run.mp == 7


def test_partial_rate_is_per_target_repeatable_and_survives_reload():
    """一対象の再送は同じ結果で、範囲内の対象は別々に抽選する。."""
    state, _ = encounter()
    marker = state.save.battle
    skill = chance_catalog(50).skills[0]
    component = skill_effects(skill)[2]
    results = {
        target: effect_activates(component, marker, target)
        for target in (f"e{i}" for i in range(40))
    }
    assert set(results.values()) == {True, False}
    restored = BattleMarker.model_validate_json(marker.model_dump_json())
    for target, expected in results.items():
        assert effect_activates(skill_effects(skill)[2], restored, target) == expected


def test_attached_status_ticks_without_another_probability_check():
    """付与済みの毒は発動率設定に関係なく、持続数の間だけ作用する。."""
    state, _ = encounter()
    state.save.run.mp = state.save.run.maxMp = 10
    marker = state.save.battle
    skill = chance_catalog(100).skills[0]
    marker.gambitAction = BattleAction(skill=skill, name=skill.name)
    apply_action(state.save, successful=True)
    marker.turn += 1
    assert poison_damage(marker, {"e": 100}) == {"e": 2}
    marker.turn += 1
    assert poison_damage(marker, {"e": 100}) == {"e": 2}


def test_probability_is_skill_owned_and_round_trips_in_catalog_and_file():
    """同じテンプレートの確率をスキルごとに変えて保存・交換できる。."""
    catalog = chance_catalog(30)
    raw = catalog.stored_document()
    assert raw["skills"][0]["effect_chances"] == {"venom": 30}
    assert all("effect_chances" not in template for template in raw["skill_templates"])
    file = SkillFile.model_validate({
        "format": "tanbunism.skills",
        "version": 6,
        "skills": raw["skills"],
        "skill_templates": raw["skill_templates"],
    })
    assert skill_effects(file.skills[0])[2].activation_chance == 30
    raw["skills"].append({
        **raw["skills"][0],
        "id": "another",
        "effect_chances": {"venom": 100},
    })
    updated = GameCatalog.model_validate(raw)
    assert [skill_effects(s)[2].activation_chance for s in updated.skills] == [30, 100]


@pytest.mark.parametrize("value", [-1, 101, True, 30.5, "30"])
def test_probability_must_be_integer_percentage(value):
    """パーセントの範囲と型を保存前に検証する。."""
    with pytest.raises(ValidationError):
        chance_catalog(value)


def test_probability_cannot_reference_unattached_template():
    """外した効果の発動率を持ち越して参照を曖昧にしない。."""
    raw = chance_catalog(30).stored_document()
    raw["skills"][0]["effect_chances"] = {"missing": 30}
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(raw)


@pytest.mark.parametrize("binding", ["hit", "cure", "venom"])
def test_rate_is_independent_for_each_component(binding):
    """ダメージ・回復・状態のどれも、他の効果とは独立して不発にできる。."""
    raw = chance_catalog(100).stored_document()
    raw["skills"][0]["effect_chances"] = {binding: 0}
    skill = GameCatalog.model_validate(raw).skills[0]
    state, context = encounter()
    state.save.run.hp = 15
    state.save.run.mp = 10
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.gambitAction = BattleAction(skill=skill, name=skill.name)
    body, results = answer(state)
    reply = resolve_scheduled(state, body, results, context, roll_player=lambda *_: 8)
    assert reply["outgoing_damage"] == ({} if binding == "hit" else {"e": 8})
    assert reply["state"].save.run.hp == (15 if binding == "cure" else 25)
    assert len(reply["state"].save.battle.effects) == (0 if binding == "venom" else 1)


@pytest.mark.parametrize("chance", [0, 100])
def test_enemy_on_hit_attachment_also_uses_probability(chance):
    """敵の命中時スキルからの追加状態にも発動率を適用する。."""
    from .combat import apply_on_hit_effects  # noqa: PLC0415

    raw = chance_catalog(chance).stored_document()
    raw["skills"][0]["template_ids"] = ["venom"]
    skill = GameCatalog.model_validate(raw).skills[0]
    state, _ = encounter()
    apply_on_hit_effects(state.save.battle, {"e": skill}, {"e": False})
    assert len(state.save.battle.effects) == int(chance == 100)
