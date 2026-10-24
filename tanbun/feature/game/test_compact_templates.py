"""Compact effect editing, target ownership and periodic recovery regressions."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .catalog import GameCatalog, parse_skill
from .effects import add_effect, apply_player_periodic
from .gambits import BattleAction, apply_action, retarget_skill
from .scheduled_combat import apply_enemy_periodic, resolve_scheduled
from .skill_outcomes import answer_effect, skill_targets
from .test_scheduled_combat import answer, encounter


def test_templates_export_count_but_not_target_and_migrate_all_enemies():
    """旧全体対象を失わず、保存JSONから対象の責務を取り除く."""
    catalog = GameCatalog.model_validate({
        "skill_templates": [
            {
                "id": "shared",
                "name": "全体",
                "effect": "attack",
                "target": "all_enemies",
            },
        ],
        "skills": [{"id": "spell", "name": "魔法", "template_ids": ["shared"]}],
    })
    raw = catalog.stored_document()
    assert "target" not in raw["skill_templates"][0]
    assert raw["skill_templates"][0]["target_count"] == "all"
    assert GameCatalog.model_validate(raw) == catalog
    assert skill_targets(catalog.skills[0], ["a", "b", "c"]) == ["a", "b", "c"]


def test_wrong_recovery_can_use_a_different_calculation_mode():
    """正答は最大HPの20%、誤答は定数5を独立して保持する."""
    skill = parse_skill({
        "id": "cure",
        "name": "回復",
        "effect": "heal_hp",
        "amount": 20,
        "amount_mode": "percent",
        "on_wrong": {"type": "reduced", "amount": 5, "amount_mode": "fixed"},
    })
    assert answer_effect(skill, correct=True).amount_mode == "percent"
    assert answer_effect(skill, correct=False).amount_mode == "fixed"
    assert answer_effect(skill, correct=False).amount == 5


def test_fixed_damage_does_not_inherit_the_success_multiplier():
    """正答の倍率と誤答の定数を混ぜない."""
    skill = parse_skill({
        "id": "spell",
        "name": "魔法",
        "effect": "attack",
        "multiplier": 2,
        "on_wrong": {
            "type": "reduced",
            "amount": 5,
            "multiplier": 0,
            "amount_mode": "fixed",
        },
    })
    assert answer_effect(skill, correct=False).multiplier == 0
    assert answer_effect(skill, correct=False).amount == 5


@pytest.mark.parametrize(("resource", "ailment"), [("hp", "regen"), ("mp", "refresh")])
def test_periodic_recovery_uses_action_duration_stacks_and_caps(resource, ailment):
    """付与した行動では発生せず、指定期間と上限内で重複する."""
    state, _ = encounter()
    marker, run = state.save.battle, state.save.run
    run.hp, run.maxHp, run.mp, run.maxMp = 10, 100, 1, 20
    skill = parse_skill({
        "id": "regen",
        "name": "継続回復",
        "effect": "ailment",
        "ailment": ailment,
        "amount": 6,
        "duration_turns": 2,
        "stack_limit": 2,
        "on_wrong": {"type": "reduced", "apply": True, "duration_turns": 1},
    })
    add_effect(marker, skill, "player")
    apply_player_periodic(run, marker, 0)
    assert getattr(run, resource) == (10 if resource == "hp" else 1)
    marker.turn += 1
    apply_player_periodic(run, marker, 0)
    assert getattr(run, resource) == (16 if resource == "hp" else 7)
    assert answer_effect(skill, correct=False).duration_turns == 1
    marker.turn += 2
    previous = getattr(run, resource)
    apply_player_periodic(run, marker, 0)
    assert getattr(run, resource) == previous
    add_effect(marker, skill, "player")
    add_effect(marker, skill, "player")
    add_effect(marker, skill, "player")
    assert len(marker.effects) == 2
    marker.turn += 1
    setattr(run, resource, getattr(run, "maxHp" if resource == "hp" else "maxMp") - 1)
    apply_player_periodic(run, marker, 0)
    assert getattr(run, resource) == getattr(
        run,
        "maxHp" if resource == "hp" else "maxMp",
    )


def test_mp_damage_and_healing_use_gambit_target_without_mutating_the_template():
    """同じ効果を自分・相手へ向け、MPと対象数をサーバーで処理する."""
    state, _ = encounter()
    marker = state.save.battle
    marker.schedule.enemy_mp = {"e": 20, "e2": 20}
    marker.schedule.enemy_max_mp = {"e": 20, "e2": 20}
    state.save.run.mp, state.save.run.maxMp = 3, 20
    skill = parse_skill({
        "id": "drain",
        "name": "MP減少",
        "effect": "damage_mp",
        "amount": 5,
        "target_count": "all",
    })
    marker.gambitAction = BattleAction(
        name=skill.name,
        skill=retarget_skill(skill, "enemy"),
    )
    apply_action(state.save, successful=True)
    assert marker.schedule.enemy_mp == {"e": 15, "e2": 15}
    assert state.save.run.mp == 3
    assert skill.target == "self"
    heal = parse_skill({
        "id": "restore",
        "name": "MP回復",
        "effect": "heal_mp",
        "amount": 10,
        "target_count": "all",
    })
    marker.gambitAction = BattleAction(
        name=heal.name,
        skill=retarget_skill(heal, "enemy"),
    )
    apply_action(state.save, successful=True)
    assert marker.schedule.enemy_mp == {"e": 20, "e2": 20}


def test_gambit_self_target_is_not_replaced_with_an_enemy():
    """対象を自分にした攻撃では敵のHPを減らさない."""
    state, context = encounter()
    state.save.battle.schedule.actor_id = "player"
    skill = parse_skill({
        "id": "self_hit",
        "name": "自分",
        "effect": "attack",
        "amount": 3,
        "multiplier": 0,
    })
    state.save.battle.gambitAction = BattleAction(
        name=skill.name,
        skill=retarget_skill(skill, "self"),
    )
    body, results = answer(state)
    reply = resolve_scheduled(state, body, results, context)
    assert reply["outgoing_damage"] == {}
    assert reply["damage"] == 3


@pytest.mark.parametrize(("effect", "expected"), [("damage_mp", 15), ("heal_hp", 50)])
def test_resource_receipt_matches_the_updated_server_snapshot(effect, expected):
    """ガンビット適用後のHP・MPを、更新前の値で応答しない."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.schedule.enemy_hp["e"] = 40
    marker.schedule.enemy_mp["e"] = 20
    marker.schedule.enemy_max_mp["e"] = 20
    context.enemies[0].mp = 20
    skill = parse_skill({
        "id": "change",
        "name": "増減",
        "effect": effect,
        "amount": 5 if effect == "damage_mp" else 10,
    })
    marker.gambitAction = BattleAction(
        name=skill.name,
        skill=retarget_skill(skill, "enemy"),
    )
    body, results = answer(state)
    reply = resolve_scheduled(state, body, results, context)
    key = "enemy_mp" if effect == "damage_mp" else "enemy_hp"
    assert reply[key]["e"] == expected
    assert getattr(reply["state"].save.battle.schedule, key)["e"] == expected


def test_mp_regeneration_recovers_after_curse_reaches_zero():
    """プレイヤー・敵とも、MP減少の下限処理後に継続回復する."""
    state, context = encounter()
    marker = state.save.battle
    state.save.run.hp, state.save.run.mp, state.save.run.maxMp = 5, 0, 20
    marker.schedule.enemy_mp["e"] = 0
    marker.schedule.enemy_max_mp["e"] = 20
    context.enemies[0].mp = 20
    for ailment, amount in [("curse", 7), ("refresh", 5)]:
        skill = parse_skill({
            "id": ailment,
            "name": ailment,
            "effect": "ailment",
            "ailment": ailment,
            "amount": amount,
        })
        add_effect(marker, skill, "player")
        add_effect(marker, skill, "e")
    marker.turn += 1
    apply_player_periodic(state.save.run, marker, 0)
    apply_enemy_periodic(state, context, marker)
    assert state.save.run.mp == 5
    assert marker.schedule.enemy_mp["e"] == 5


@pytest.mark.parametrize(
    ("field", "value"),
    [("amount", -1), ("target_count", 0), ("target_count", 6)],
)
def test_negative_values_and_invalid_counts_are_rejected(field, value):
    """負の効果量を回復の別表現として受け付けない."""
    with pytest.raises(ValidationError):
        parse_skill({"id": "bad", "name": "不正", "effect": "heal_hp", field: value})
