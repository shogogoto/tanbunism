"""条件を満たした相手と行動の発動先、および条件の変型。."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .catalog import (
    AilmentSkill,
    GameCatalog,
    PlayerSkillUse,
    TriggerDefinition,
    default_catalog,
)
from .effects import BattleEffect
from .end_actions import begin_action, finish_action
from .gambits import GambitRule, apply_action, choose_action
from .scheduled_combat import resolve_scheduled
from .test_scheduled_combat import answer, encounter


def targeted(condition="hp_below", target="enemy", **kwargs):
    """一体目ではなく二体目の敵だけが条件を満たす戦闘。."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.schedule.enemy_hp = {"e": 100, "e2": 29}
    state.save.run.hp = state.save.run.maxHp
    state.save.run.mp = 10
    catalog = default_catalog()
    catalog.triggers = [
        TriggerDefinition(
            id="test",
            name="条件",
            condition=condition,
            target=target,
            **kwargs,
        ),
    ]
    state.save.gambits = [GambitRule(trigger_id="test", definition_id="fire")]
    return state, context, catalog


def test_enemy_hp_condition_attacks_matching_enemy_not_first_or_player():
    """一体目やプレイヤーではなく、HP条件に合う二体目へ攻撃する。."""
    state, context, catalog = targeted()
    marker = state.save.battle
    marker.gambitAction = choose_action(state.save, catalog)
    assert marker.gambitAction.target_ids == ["e2"]
    body, results = answer(state)
    reply = resolve_scheduled(state, body, results, context, roll_player=lambda *_: 10)
    assert set(reply["outgoing_damage"]) == {"e2"}
    assert reply["state"].save.battle.schedule.enemy_hp["e"] == 100


@pytest.mark.parametrize("target", ["self", "ally"])
def test_ally_includes_player_and_self_targets_player(target):
    """味方指定でも自分自身を回復対象に含める。."""
    state, _, catalog = targeted(target=target)
    assert choose_action(state.save, catalog) is None
    state.save.run.hp = 1
    state.save.gambits[0].definition_id = "heal"
    action = choose_action(state.save, catalog)
    assert action.target_ids == ["player"]
    assert action.skill.target == "self"
    state.save.battle.gambitAction = action
    apply_action(state.save, successful=True)
    assert state.save.run.hp > 1


def test_enemy_mp_uses_enemy_resource_and_excludes_zero_maximum():
    """最大MPゼロを割合判定の対象にしない。."""
    state, _, catalog = targeted(condition="mp_below")
    schedule = state.save.battle.schedule
    schedule.enemy_mp = {"e": 0, "e2": 2}
    schedule.enemy_max_mp = {"e": 0, "e2": 10}
    assert choose_action(state.save, catalog).target_ids == ["e2"]


def test_status_condition_and_application_use_same_enemy():
    """状態の判定対象にだけ、新しい状態を付与する。."""
    state, _, catalog = targeted(condition="state", state_skill_id="poison")
    poison = AilmentSkill(id="poison", name="毒", effect="ailment", ailment="poison")
    catalog.skills.append(poison)
    catalog.player_skills.append(PlayerSkillUse(skill_id="poison"))
    marker = state.save.battle
    marker.effects = [
        BattleEffect(skill=poison, target_id="e2", starts_turn=0, expires_turn=5),
    ]
    state.save.gambits[0].definition_id = "poison"
    marker.gambitAction = choose_action(state.save, catalog)
    assert marker.gambitAction.target_ids == ["e2"]
    apply_action(state.save, successful=True)
    assert all(e.target_id == "e2" for e in marker.effects)
    marker.effects = []
    assert choose_action(state.save, catalog) is None


def test_area_attack_only_affects_enemies_matching_condition():
    """範囲攻撃も条件外の敵には当たらず、結果の対象も一致する。."""
    state, _, catalog = targeted()
    state.save.gambits[0].definition_id = "fire_all"
    selected = choose_action(state.save, catalog)
    assert selected.target_ids == ["e2"]
    marker = state.save.battle
    begin_action(state.save, selected, selected.skill, [], marker.schedule.enemy_hp)
    outgoing = finish_action(state)
    assert set(outgoing) == {"e2"}
    assert state.save.endActionReceipt["target_ids"] == ["e2"]


def test_enemy_recovery_and_full_target_skip():
    """敵の回復は敵の現在値へ反映し、満タンなら次のルールへ進む。."""
    state, context, catalog = targeted(condition="always")
    state.save.gambits[0].definition_id = "heal"
    marker = state.save.battle
    marker.gambitAction = choose_action(state.save, catalog)
    assert marker.gambitAction.target_ids == ["e2"]
    apply_action(state.save, successful=True, context=context)
    assert marker.schedule.enemy_hp == {"e": 100, "e2": 49}
    marker.schedule.enemy_hp["e2"] = 100
    assert choose_action(state.save, catalog) is None


def test_variants_override_one_field_and_deleted_candidate_skips_rule():
    """管理定義のしきい値候補を参照し、失われた候補で実行しない。."""
    state, _, catalog = targeted(variants=[{"id": "half", "name": "半分", "value": 50}])
    marker = state.save.battle
    marker.schedule.enemy_hp["e2"] = 40
    assert choose_action(state.save, catalog) is None
    rule = state.save.gambits[0]
    rule.trigger_variant_id = "half"
    rule.threshold = 1  # Player cannot override an admin-defined candidate.
    assert choose_action(state.save, catalog).target_ids == ["e2"]
    catalog.triggers[0].variants = []
    assert choose_action(state.save, catalog) is None


def test_target_variant_overrides_condition_and_action_together():
    """対象の変型は判定先と発動先を同時に変更する。."""
    state, _, catalog = targeted(
        target="self",
        variant_field="target",
        variants=[{"id": "foe", "name": "敵", "value": "enemy"}],
    )
    assert choose_action(state.save, catalog) is None
    state.save.gambits[0].trigger_variant_id = "foe"
    action = choose_action(state.save, catalog)
    assert action.target_ids == ["e2"]
    assert action.skill.target == "enemy"


def test_state_presence_variant_checks_absence_of_selected_skill():
    """状態スキルを指定し、「なし」の変型で対象の状態不在を調べる。."""
    state, _, catalog = targeted(
        condition="state",
        state_skill_id="poison",
        variant_field="state_present",
        variants=[{"id": "absent", "name": "なし", "value": False}],
    )
    catalog.skills.append(
        AilmentSkill(id="poison", name="毒", effect="ailment", ailment="poison"),
    )
    assert choose_action(state.save, catalog) is None
    state.save.gambits[0].trigger_variant_id = "absent"
    assert choose_action(state.save, catalog).target_ids == ["e", "e2"]


def test_enemy_counter_uses_the_matching_enemy_not_the_quiz_source():
    """出題元が一体目でも、反撃は実際に狙った二体目から受ける。."""
    state, context, catalog = targeted()
    context.enemies[0].attack = 5
    context.enemies[1].attack = 31
    state.save.gambits[0].definition_id = "normal_attack"
    catalog.player_skills.append(PlayerSkillUse(skill_id="normal_attack"))
    marker = state.save.battle
    marker.gambitAction = choose_action(state.save, catalog)
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(
        state,
        body,
        results,
        context,
        roll_enemy=lambda attack, *_: attack,
    )
    assert reply["damage"] == 31


def test_end_action_can_target_self_without_treating_player_as_enemy():
    """味方指定の攻撃で敵HPを参照せず、戦闘不能も処理できる。."""
    state, _, catalog = targeted(condition="always", target="ally")
    state.save.run.hp = 1
    selected = choose_action(state.save, catalog)
    marker = state.save.battle
    begin_action(state.save, selected, selected.skill, [], marker.schedule.enemy_hp)
    assert finish_action(state) == {}
    assert state.save.run.hp == 0
    assert state.save.run.phase == "defeated"
    assert state.save.battle is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("threshold", -1),
        ("threshold", True),
        ("threshold", "30"),
        ("target", "other"),
        ("condition", "other"),
        ("state_present", 1),
        ("state_skill_id", "not a valid id"),
    ],
)
def test_invalid_variant_rejected(field, value):
    """変型も元の項目と同じ型と範囲を満たす必要がある。."""
    with pytest.raises(ValidationError):
        TriggerDefinition(
            id="test",
            name="条件",
            variant_field=field,
            variants=[{"id": "v", "name": "候補", "value": value}],
        )


def test_variant_state_skill_references_are_validated_even_when_unused():
    """未選択の候補からも存在しないスキルを参照させない。."""
    raw = default_catalog().model_dump()
    raw["triggers"] = [
        {
            "id": "test",
            "name": "状態",
            "condition": "state",
            "variant_field": "state_skill_id",
            "variants": [{"id": "missing", "name": "不明", "value": "missing"}],
        },
    ]
    with pytest.raises(ValidationError, match="状態系スキル"):
        GameCatalog.model_validate(raw)
