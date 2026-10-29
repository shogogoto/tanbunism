"""敵にも条件と対象を一致させた優先順位付きの自動行動を与える。."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .catalog import (
    BuffSkill,
    EnemyDefinition,
    EnemyGambitRule,
    GameCatalog,
    TriggerDefinition,
    default_catalog,
)
from .combat import style_enemies
from .enemy_gambits import choose_enemy_action, enemy_battle_rules
from .scheduled_combat import begin_scheduled_action, resolve_scheduled
from .test_scheduled_combat import answer, encounter


def configured(target="self", condition="hp_below", skill_id="heal"):
    """敵一体に、低HPを回復する設定を固定する。."""
    state, context = encounter()
    catalog = default_catalog()
    catalog.triggers = [
        TriggerDefinition(
            id="hurt",
            name="HPが少ない",
            condition=condition,
            target=target,
        ),
    ]
    definition = EnemyDefinition(
        id="healer",
        name="回復役",
        gambits=[EnemyGambitRule(trigger_id="hurt", skill_id=skill_id)],
    )
    context.enemies[0].gambits = enemy_battle_rules(definition, catalog)
    marker = state.save.battle
    marker.schedule.enemy_hp = {"e": 20, "e2": 100}
    marker.schedule.enemy_mp["e"] = 10
    marker.schedule.enemy_max_mp["e"] = 10
    context.enemies[0].mp = 10
    marker.schedule.waits = {"player": 10, "e": 0, "e2": 10}
    begin_scheduled_action(state, context)
    assert marker.schedule.actor_id == "e"
    return state, context, catalog


@pytest.mark.parametrize("correct", [True, False])
def test_enemy_self_heal_is_sealed_and_spends_mp_once(correct):
    """誤答で敵が自分を回復し、正答で不発でもMPは一度消費する。."""
    state, context, _ = configured()
    marker = state.save.battle
    assert marker.enemyAction.target_ids == ["e"]
    assert marker.enemyAction.skill.id == "heal"
    context.enemies[0].gambits[0].skill.amount = 999
    body, results = answer(state, correct=correct)
    reply = resolve_scheduled(state, body, results, context)
    schedule = reply["state"].save.battle.schedule
    assert schedule.enemy_hp["e"] == (20 if correct else 40)
    assert schedule.enemy_mp["e"] == 7
    assert reply["damage"] == 0
    feedback = reply["state"].save.battleFeedback
    assert "eの" in feedback
    assert ("不発" if correct else "発動") in feedback


def test_ally_includes_actor_but_only_matching_ally_is_healed():
    """味方指定は本人も含み、条件に合った二体目を直接回復できる。."""
    state, context, _ = configured(target="ally")
    marker = state.save.battle
    assert marker.enemyAction.target_ids == ["e"]
    marker.schedule.enemy_hp = {"e": 100, "e2": 20}
    marker.schedule.waits = {"player": 10, "e": 0, "e2": 10}
    begin_scheduled_action(state, context)
    assert marker.enemyAction.target_ids == ["e2"]
    player_hp = state.save.run.hp
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(state, body, results, context)
    save = reply["state"].save
    assert save.battle.schedule.enemy_hp == {"e": 100, "e2": 40}
    assert save.run.hp == player_hp


def test_enemy_targets_player_and_uses_special_skill():
    """敵から見た「敵」はプレイヤーであり、設定した魔法で攻撃する。."""
    state, context, _ = configured(target="enemy", condition="always", skill_id="fire")
    assert state.save.battle.enemyAction.target_ids == ["player"]
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(state, body, results, context, roll_enemy=lambda *_: 5)
    assert reply["damage"] > 0
    assert reply["outgoing_damage"] == {}
    assert reply["state"].save.battle.schedule.enemy_mp["e"] == 7


@pytest.mark.parametrize("correct", [True, False])
@pytest.mark.parametrize("reduced", [True, False])
def test_enemy_haste_reports_actor_and_actual_outcome(correct, reduced):
    """加速は名前だけで終わらず、正答時の不発・軽減と誤答時の発動を伝える."""
    state, context, _ = configured(condition="always")
    marker = state.save.battle
    marker.enemyAction.skill = BuffSkill(
        id="haste",
        name="加速",
        effect="buff",
        stat="speed",
        amount=30,
        amount_mode="percent",
        duration_turns=4,
        mp_cost=3,
        on_wrong={
            "type": "reduced",
            "duration_turns": 2,
            "amount": 10,
            "amount_mode": "percent",
        }
        if reduced
        else {"type": "fizzle"},
    )
    marker.enemyAction.name = "加速"
    context.enemies[0].name = "ゴブリン"
    body, results = answer(state, correct=correct)
    reply = resolve_scheduled(state, body, results, context)
    save = reply["state"].save
    expected = "軽減" if correct and reduced else "不発" if correct else "発動"
    assert save.battleFeedback == f"ゴブリンの加速 · {expected}"
    assert save.quizFeedback == ("正解" if correct else "不正解")
    assert save.battle.schedule.enemy_mp["e"] == 7
    effects = [effect for effect in save.battle.effects if effect.skill.id == "haste"]
    assert bool(effects) is (not correct or reduced)
    if effects:
        assert effects[0].skill.duration_turns == (2 if correct else 4)


def test_mp_shortage_skips_to_next_rule_and_unmatched_rules_fall_back():
    """MP不足は次の行へ進み、全件使えないなら従来の通常攻撃へ戻る。."""
    state, context, catalog = configured()
    enemy = context.enemies[0]
    state.save.battle.schedule.enemy_mp["e"] = 0
    assert choose_enemy_action(state.save, enemy) is None
    extra = EnemyDefinition(
        id="extra",
        name="通常",
        gambits=[EnemyGambitRule(trigger_id="hurt", skill_id="normal_attack")],
    )
    enemy.gambits += enemy_battle_rules(extra, catalog)
    selected = choose_enemy_action(state.save, enemy)
    assert selected.skill.id == "normal_attack"
    enemy.gambits[1].enabled = False
    assert choose_enemy_action(state.save, enemy) is None


def test_variant_and_state_condition_definitions_are_frozen_in_encounter():
    """敵もトリガーの変型を参照し、素材の装飾から戦闘設定へ引き継ぐ。."""
    _state, context = encounter()
    raw = default_catalog().stored_document()
    raw["triggers"] = [
        {
            "id": "hurt",
            "name": "HP",
            "condition": "hp_below",
            "target": "self",
            "threshold": 10,
            "variants": [{"id": "half", "name": "半分", "value": 50}],
        },
    ]
    raw["enemies"] = [
        {
            "id": "healer",
            "name": "回復役",
            "gambits": [
                {
                    "trigger_id": "hurt",
                    "trigger_variant_id": "half",
                    "skill_id": "heal",
                },
            ],
        },
    ]
    catalog = GameCatalog.model_validate(raw)
    styled = style_enemies(context.enemies, catalog)
    assert styled[0].gambits[0].threshold == 50
    catalog.triggers[0].variants[0].value = 99
    assert styled[0].gambits[0].threshold == 50


@pytest.mark.parametrize("missing", ["skill", "trigger", "variant"])
def test_invalid_enemy_gambit_references_cannot_be_saved(missing):
    """存在しないスキル・条件・候補の参照を管理保存時に拒否する。."""
    raw = default_catalog().stored_document()
    rule = {"trigger_id": "always", "skill_id": "heal"}
    rule[
        {"skill": "skill_id", "trigger": "trigger_id", "variant": "trigger_variant_id"}[
            missing
        ]
    ] = "missing"
    raw["enemies"][0]["gambits"] = [rule]
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(raw)
