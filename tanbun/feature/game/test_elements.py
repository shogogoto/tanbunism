"""属性の定義・弱点・持続補正は、利用経路によらず同じ結果になる."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .catalog import (
    AttackSkill,
    BuffSkill,
    GameCatalog,
    RecoverySkill,
    default_catalog,
    parse_skill,
)
from .effects import add_effect, apply_resource_effect, elemental_damage
from .gambits import BattleAction
from .inventory import Economy, EquipmentBuff, equipped_stats
from .scheduled_combat import resolve_scheduled
from .skill_files import SkillFilePreview
from .skill_outcomes import answer_effect, skill_effects
from .test_scheduled_combat import answer, encounter


def elemental_buff(**patch):
    """行動自体は無属性でも、炎への補正を定義できる."""
    return parse_skill({
        "id": "modifier",
        "name": "属性補正",
        "effect": "buff",
        "stat": "damageDealt",
        "affected_element": "fire",
        "amount": 25,
        "amount_mode": "percent",
        **patch,
    })


def test_elements_default_and_invalid_references():
    """基本八属性と無属性を区別し、削除による参照切れを拒否する."""
    catalog = default_catalog()
    assert [element.id for element in catalog.elements] == [
        "fire",
        "ice",
        "lightning",
        "water",
        "wind",
        "earth",
        "light",
        "dark",
    ]
    raw = catalog.model_dump()
    for key, change in [
        ("skills", {"element": "missing"}),
        ("enemies", {"weaknesses": ["missing"]}),
    ]:
        changed = {**raw, key: [{**raw[key][0], **change}, *raw[key][1:]]}
        with pytest.raises(ValidationError, match="属性"):
            GameCatalog.model_validate(changed)
    with pytest.raises(ValidationError, match="予約"):
        GameCatalog.model_validate({**raw, "elements": [{"id": "none", "name": "無"}]})
    with pytest.raises(ValidationError, match="重複"):
        GameCatalog.model_validate({
            **raw,
            "elements": [*raw["elements"], raw["elements"][0]],
        })


@pytest.mark.parametrize(
    "patch",
    [
        {"amount_mode": "fixed"},
        {"affected_element": None},
        {"affected_element": "none"},
        {"stat": "attack"},
    ],
)
def test_attribute_modifiers_require_percentage_and_element(patch):
    """能力バフと属性バフの項目を混在させない."""
    with pytest.raises(ValidationError):
        elemental_buff(**patch)
    assert "affected_element" not in BuffSkill(id="plain", name="能力").model_dump()


def test_custom_elements_roundtrip_and_composite_skill_identity():
    """独自属性もJSON交換でき、スキルの属性が複合効果へ引き継がれる."""
    catalog = default_catalog()
    imported = SkillFilePreview(
        catalog=catalog,
        document={
            "format": "tanbunism.skills",
            "version": 5,
            "elements": [{"id": "metal", "name": "金属", "color": "#889999"}],
            "skills": [
                {
                    "id": "metal_attack",
                    "name": "金属攻撃",
                    "effect": "attack",
                    "element": "metal",
                    "effects": [
                        {"effect": "attack"},
                        {
                            "effect": "buff",
                            "stat": "damageTaken",
                            "affected_element": "fire",
                            "amount": 25,
                            "amount_mode": "percent",
                        },
                    ],
                },
            ],
        },
    ).merged_catalog()
    restored = GameCatalog.model_validate_json(imported.model_dump_json())
    assert restored == imported
    skill = next(skill for skill in restored.skills if skill.id == "metal_attack")
    effects = skill_effects(skill)
    assert all(effect.element == "metal" for effect in effects)
    assert effects[1].affected_element == "fire"


@pytest.mark.parametrize(
    ("element", "enabled", "expected"),
    [("fire", None, 30), ("ice", None, 20), ("none", None, 20), ("fire", [], 20)],
)
def test_weakness_applies_once_and_disabled_elements_are_neutral(
    element,
    enabled,
    expected,
):
    """非弱点は等倍。弱点の重複や無効な属性で倍率を増やさない."""
    skill = AttackSkill(id="attack", name="攻撃", element=element)
    assert (
        elemental_damage(
            20,
            skill,
            None,
            "player",
            "enemy",
            weaknesses=["fire", "fire"],
            enabled_elements=enabled,
        )
        == expected
    )


def test_element_buffs_stack_additively_and_expire_without_changing_stats():
    """同方向は加算、与・被の間は乗算。有効期間外と別属性は影響しない."""
    state, _ = encounter()
    marker = state.save.battle
    fire = AttackSkill(id="fire", name="炎", element="fire")
    add_effect(marker, elemental_buff(stack_limit=2), "player")
    add_effect(marker, elemental_buff(stack_limit=2), "player")
    add_effect(marker, elemental_buff(id="resist", stat="damageTaken", amount=20), "e")
    assert elemental_damage(20, fire, marker, "player", "e") == 20
    marker.turn += 1
    assert elemental_damage(20, fire, marker, "player", "e", weaknesses=["fire"]) == 36
    assert (
        elemental_damage(
            20,
            fire.model_copy(update={"element": "ice"}),
            marker,
            "player",
            "e",
        )
        == 20
    )
    marker.turn += 3
    assert elemental_damage(20, fire, marker, "player", "e") == 20


@pytest.mark.parametrize(
    ("stat", "source", "target", "expected"),
    [("damageDealt", "e", "player", 15), ("damageTaken", "player", "e", 25)],
)
def test_debuff_direction_and_wrong_answer_reduction(stat, source, target, expected):
    """与ダメージ低下と被ダメージ増加。軽減後も対象属性を維持する."""
    state, _ = encounter()
    marker = state.save.battle
    effect = elemental_buff(
        effect="debuff",
        stat=stat,
        on_wrong={"type": "reduced", "amount": 25, "duration_turns": 1},
    )
    reduced = answer_effect(effect, correct=False)
    add_effect(marker, reduced, "e")
    marker.turn += 1
    assert (
        elemental_damage(
            20,
            AttackSkill(id="fire", name="炎", element="fire"),
            marker,
            source,
            target,
        )
        == expected
    )
    marker.turn += 1
    assert (
        elemental_damage(
            20,
            AttackSkill(id="fire", name="炎", element="fire"),
            marker,
            source,
            target,
        )
        == 20
    )


def test_scheduled_damage_uses_weakness_and_player_resistance_on_both_sides():
    """実戦と模擬戦が呼ぶCTB精算で、弱点と受ける側の補正を適用する."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    target = marker.schedule.quiz_enemy_id
    next(enemy for enemy in context.enemies if enemy.id == target).weaknesses = ["fire"]
    fire = AttackSkill(id="fire", name="炎", element="fire")
    marker.gambitAction = BattleAction(skill=fire, name="炎")
    body, results = answer(state)
    reply = resolve_scheduled(state, body, results, context, roll_player=lambda *_: 12)
    assert reply["outgoing_damage"] == {target: 18}
    marker.schedule.actor_id = target
    next(enemy for enemy in context.enemies if enemy.id == target).attack_skill = fire
    add_effect(marker, elemental_buff(stat="damageTaken", amount=50), "player")
    marker.turn += 1
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(state, body, results, context, roll_enemy=lambda *_: 10)
    assert reply["damage"] == 5


def test_player_mp_attack_uses_the_enemy_weakness():
    """ガンビット経由のMP攻撃も、直接のリソース効果と同じ倍率で精算する."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    target = marker.schedule.quiz_enemy_id
    enemy = next(enemy for enemy in context.enemies if enemy.id == target)
    enemy.weaknesses = ["fire"]
    enemy.mp = 20
    marker.schedule.enemy_max_mp[target] = 20
    marker.schedule.enemy_mp[target] = 20
    skill = RecoverySkill(
        id="burn",
        name="炎MP",
        effect="damage_mp",
        element="fire",
        amount=8,
        target="enemy",
    )
    marker.gambitAction = BattleAction(skill=skill, name=skill.name)
    body, results = answer(state)
    reply = resolve_scheduled(state, body, results, context)
    assert reply["enemy_mp"][target] == 8


def test_mp_damage_and_equipment_use_the_same_element_rules():
    """属性耐性装備はHPだけでなくMPダメージにも有効。能力辞書を汚さない."""
    state, context = encounter()
    state.save.run.mp = 10
    state.save.run.maxMp = 10
    state.save.economy = Economy(
        weapon="ring",
        inventory={"ring": 1},
        equipment_buffs=[
            EquipmentBuff(
                stat="damageTaken",
                affected_element="fire",
                amount=50,
                amount_mode="percent",
            ),
        ],
    )
    skill = RecoverySkill(
        id="burn",
        name="炎MP",
        effect="damage_mp",
        element="fire",
        amount=8,
        target="enemy",
    )
    apply_resource_effect(state.save, skill, "e", context=context)
    assert state.save.run.mp == 6
    assert "damageTaken" not in equipped_stats(
        state.save.allocation,
        context.balance,
        state.save.economy,
    )
