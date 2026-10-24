"""敵の属性相性は弱点・中立・耐性の三値。全精算経路で同じ倍率を使う."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .catalog import (
    AttackSkill,
    EnemyDefinition,
    GameCatalog,
    RecoverySkill,
    default_catalog,
)
from .combat import style_enemies
from .effects import elemental_damage
from .end_actions import begin_action, finish_action
from .gambits import BattleAction
from .scheduled_combat import resolve_scheduled, self_attack_damage
from .test_scheduled_combat import answer, encounter


def test_enemy_affinities_are_exclusive_and_references_validated():
    """旧弱点を維持し、競合や存在しない耐性属性を拒否する."""
    enemy = EnemyDefinition(id="ghost", name="敵", weaknesses=["fire"])
    assert enemy.resistances == []
    with pytest.raises(ValidationError, match="両方"):
        EnemyDefinition(
            id="ghost",
            name="敵",
            weaknesses=["fire"],
            resistances=["fire"],
        )
    raw = default_catalog().model_dump()
    raw["enemies"][0]["resistances"] = ["unknown"]
    with pytest.raises(ValidationError, match="属性"):
        GameCatalog.model_validate(raw)
    raw["enemies"][0]["resistances"] = ["ice"]
    assert GameCatalog.model_validate_json(
        GameCatalog.model_validate(raw).model_dump_json(),
    ).enemies[0].resistances == ["ice"]


@pytest.mark.parametrize(
    ("element", "enabled", "expected"),
    [("fire", None, 10), ("ice", None, 20), ("none", None, 20), ("fire", [], 20)],
)
def test_resistance_only_affects_enabled_matching_elements(element, enabled, expected):
    """重複で倍率を二重適用せず、無属性・別属性・無効属性は等倍."""
    skill = AttackSkill(id="hit", name="攻撃", element=element)
    assert (
        elemental_damage(
            20,
            skill,
            None,
            "player",
            "enemy",
            resistances=["fire", "fire"],
            enabled_elements=enabled,
        )
        == expected
    )


def test_styled_enemies_keep_resistances_in_live_and_practice():
    """能力の試算値を保持する模擬戦にも、同じ敵定義の相性が反映される."""
    _, context = encounter()
    catalog = GameCatalog(
        enemies=[
            EnemyDefinition(
                id="ghost",
                name="敵",
                weaknesses=["fire"],
                resistances=["ice"],
            ),
        ],
    )
    for preserve in [True, False]:
        enemy = style_enemies(context.enemies, catalog, preserve_stats=preserve)[0]
        assert (enemy.weaknesses, enemy.resistances) == (["fire"], ["ice"])
    disabled = catalog.model_copy(deep=True)
    next(
        element for element in disabled.elements if element.id == "ice"
    ).enabled = False
    assert style_enemies(context.enemies, disabled)[0].resistances == []


@pytest.mark.parametrize("effect", ["attack", "damage_mp"])
def test_scheduled_attack_uses_configured_resistance_for_hp_and_mp(effect):
    """実戦と模擬戦のCTB精算。耐性倍率は管理設定に従う."""
    state, context = encounter()
    context.balance = context.balance.model_copy(update={"resistance_multiplier": 0.25})
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    target = marker.schedule.quiz_enemy_id
    enemy = next(enemy for enemy in context.enemies if enemy.id == target)
    enemy.resistances = ["fire"]
    enemy.mp = 20
    marker.schedule.enemy_mp[target] = 20
    marker.schedule.enemy_max_mp[target] = 20
    skill = (
        AttackSkill(id="fire", name="炎", element="fire")
        if effect == "attack"
        else RecoverySkill(
            id="fire",
            name="炎MP",
            element="fire",
            effect="damage_mp",
            target="enemy",
            amount=20,
        )
    )
    marker.gambitAction = BattleAction(skill=skill, name=skill.name)
    body, results = answer(state)
    reply = resolve_scheduled(state, body, results, context, roll_player=lambda *_: 20)
    if effect == "attack":
        assert reply["outgoing_damage"] == {target: 5}
    else:
        assert reply["enemy_mp"][target] == 15


def test_end_action_snapshots_resistance_and_enemy_self_attack_respects_it():
    """専門分野の精算と敵が自分へ攻撃する場合にも、対象の耐性を使う."""
    state, context = encounter()
    marker = state.save.battle
    for enemy in context.enemies:
        enemy.resistances = ["fire"]
    skill = AttackSkill(id="fire", name="炎", element="fire")
    expected = state.save.run.attack // 2
    begin_action(
        state.save,
        BattleAction(skill=skill, name=skill.name),
        skill,
        [],
        marker.schedule.enemy_hp,
        context=context,
    )
    context.enemies[0].resistances.clear()
    assert finish_action(state)[marker.enemies[0]] == expected
    context.enemies[0].resistances = ["fire"]
    assert (
        self_attack_damage(
            state,
            context,
            skill,
            schedule_actor=context.enemies[0].id,
            roll_enemy=lambda *_: 20,
        )
        == 10
    )
