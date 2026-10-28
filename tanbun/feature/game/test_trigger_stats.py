"""能力比較・残量割合・順位で、発動先をサーバーが選ぶ."""

import pytest
from pydantic import ValidationError

from .catalog import GameCatalog, TriggerDefinition, default_catalog, parse_skill
from .effects import add_effect, sync_speed_effects
from .enemy_gambits import choose_enemy_action, enemy_battle_rules
from .gambits import GambitRule, choose_action, condition_matches, effective_rule
from .scheduled_combat import initialize_schedule
from .skill_outcomes import skill_targets
from .test_scheduled_combat import encounter


def stat_battle(stat="defense", comparison="lowest", **changes):
    """四体を用意し、二体目と四体目を同率にする."""
    state, context = encounter()
    context.enemies = [
        context.enemies[0].model_copy(update={"id": f"e{i}", stat: value})
        for i, value in enumerate([20, 5, 10, 5], start=1)
    ]
    state.save.battle.enemies = [e.id for e in context.enemies]
    initialize_schedule(state, context)
    state.save.run.mp = 100
    catalog = default_catalog()
    catalog.triggers = [
        TriggerDefinition(
            id="stat",
            name="能力",
            condition="stat",
            stat=stat,
            comparison=comparison,
            target="enemy",
            **changes,
        ),
    ]
    state.save.gambits = [GambitRule(trigger_id="stat", definition_id="fire")]
    return state, context, catalog


@pytest.mark.parametrize(
    "stat",
    ["attack", "defense", "special_attack", "special_defense", "speed"],
)
@pytest.mark.parametrize(
    ("comparison", "expected"),
    [
        ("lowest", ["e2", "e4", "e3", "e1"]),
        ("highest", ["e1", "e3", "e2", "e4"]),
    ],
)
def test_each_stat_can_rank_targets_and_preserves_ties(stat, comparison, expected):
    """全能力で同じ順序を用い、対象数に応じて先頭から選ぶ."""
    state, _, catalog = stat_battle(stat, comparison)
    action = choose_action(state.save, catalog)
    assert action.target_ids == expected
    assert (
        skill_targets(action.skill, state.save.battle.enemies, action.target_ids)
        == expected[:1]
    )
    action.skill.target_count = 3
    assert (
        skill_targets(action.skill, state.save.battle.enemies, action.target_ids)
        == expected[:3]
    )


@pytest.mark.parametrize(
    ("comparison", "threshold", "expected"),
    [
        ("below", 10, ["e2", "e4"]),
        ("above", 10, ["e1", "e3"]),
        ("equal", 10, ["e3"]),
    ],
)
def test_stat_comparisons_have_disjoint_below_and_above_boundaries(
    comparison,
    threshold,
    expected,
):
    """未満は境界を除き、以上・一致は境界を含む."""
    state, _, catalog = stat_battle(comparison=comparison, threshold=threshold)
    assert choose_action(state.save, catalog).target_ids == expected


def test_rank_uses_buffs_and_excludes_defeated_targets():
    """生存者だけを、バフ適用後の能力で並べる."""
    state, _, catalog = stat_battle()
    marker = state.save.battle
    add_effect(
        marker,
        parse_skill({
            "id": "guard",
            "name": "守",
            "effect": "buff",
            "stat": "defense",
            "amount": 50,
        }),
        "e2",
    )
    marker.turn += 1
    marker.schedule.enemy_hp["e4"] = 0
    assert choose_action(state.save, catalog).target_ids == ["e3", "e1", "e2"]


def test_speed_rank_uses_debuffed_queue_speed():
    """速さも行動キューが使う有効値で比較し、二重補正しない."""
    state, _, catalog = stat_battle("speed")
    marker = state.save.battle
    add_effect(
        marker,
        parse_skill({
            "id": "slow",
            "name": "遅",
            "effect": "debuff",
            "stat": "speed",
            "amount": 19,
        }),
        "e1",
    )
    marker.turn += 1
    sync_speed_effects(marker)
    assert choose_action(state.save, catalog).target_ids[0] == "e1"


@pytest.mark.parametrize(
    ("basis", "expected"),
    [("current", ["e1", "e2"]), ("maximum", ["e1", "e2"]), ("percent", ["e2", "e1"])],
)
def test_hp_rank_can_compare_remaining_maximum_or_percentage(basis, expected):
    """20/40と30/100では、HP実数値と残量割合の順位が逆になる."""
    state, _, catalog = stat_battle("hp", stat_basis=basis)
    marker = state.save.battle
    marker.enemies = ["e1", "e2"]
    marker.schedule.enemy_hp = {"e1": 20, "e2": 30}
    marker.schedule.enemy_max_hp = {"e1": 40, "e2": 100}
    assert choose_action(state.save, catalog).target_ids == expected


def test_zero_mp_and_values_above_one_hundred_are_supported():
    """MP0や100を超える絶対値も条件にできる."""
    state, _, catalog = stat_battle("mp", "equal", threshold=0)
    state.save.battle.schedule.enemy_mp["e3"] = 0
    assert choose_action(state.save, catalog).target_ids == ["e3"]
    rule = GambitRule(
        condition="stat",
        stat="defense",
        comparison="above",
        threshold=150,
        definition_id="heal",
    )
    state.save.battle.schedule.stat_bases["e1"]["defense"] = 200
    assert condition_matches(rule, state.save, {}, "e1")


def test_enemy_uses_same_rank_in_its_own_ally_camp():
    """敵も、本人を含む味方を能力順に選べる."""
    state, context, catalog = stat_battle()
    catalog.triggers[0].target = "ally"
    enemy = context.enemies[0]
    state.save.battle.schedule.enemy_mp[enemy.id] = 10
    from .catalog import EnemyDefinition, EnemyGambitRule  # noqa: PLC0415

    definition = EnemyDefinition(
        id="mage",
        name="魔",
        gambits=[EnemyGambitRule(trigger_id="stat", skill_id="fire")],
    )
    enemy.gambits = enemy_battle_rules(definition, catalog)
    assert choose_enemy_action(state.save, enemy).target_ids == ["e2", "e4", "e3", "e1"]


def test_stat_comparison_variants_and_sets_preserve_selected_field():
    """既存HP条件にも共有の比較・順位セットを適用できる."""
    catalog = GameCatalog.model_validate({
        "trigger_variant_sets": [
            {
                "id": "rank",
                "name": "順位",
                "field": "comparison",
                "values": [{"id": "low", "name": "低", "value": "lowest"}],
            },
        ],
        "triggers": [
            {
                "id": "hp",
                "name": "HP",
                "condition": "hp_below",
                "target": "enemy",
                "variant_set_id": "rank",
            },
        ],
    })
    rule = effective_rule(
        GambitRule(trigger_id="hp", trigger_variant_id="low", definition_id="heal"),
        catalog,
    )
    assert (rule.condition, rule.stat, rule.stat_basis, rule.comparison) == (
        "stat",
        "hp",
        "percent",
        "lowest",
    )
    assert GameCatalog.model_validate(catalog.stored_document()) == catalog


def test_non_resource_stats_cannot_compare_percentages():
    """守備力にHPのような残量割合は存在しない."""
    with pytest.raises(ValidationError):
        TriggerDefinition(
            id="bad",
            name="不正",
            condition="stat",
            stat="defense",
            stat_basis="percent",
        )
