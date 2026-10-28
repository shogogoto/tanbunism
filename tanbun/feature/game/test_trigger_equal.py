"""割合の一致条件と最大HP/MP変化時のガンビット判定."""
# ruff: noqa: PLR2004

import pytest

from .catalog import GameCatalog, parse_skill
from .effects import add_effect, sync_bubble
from .gambits import GambitRule, condition_matches
from .test_scheduled_combat import encounter


@pytest.mark.parametrize("resource", ["hp", "mp"])
@pytest.mark.parametrize(
    ("current", "maximum", "matches"),
    [
        (50, 100, True),
        (100, 200, True),
        (49, 100, False),
        (51, 100, False),
        (50, 101, False),
        (1, 3, False),
        (0, 0, False),
    ],
)
def test_equal_is_exact_and_never_rounds_percentages(
    resource,
    current,
    maximum,
    matches,
):
    """整数同士で比較し、約50%やゼロ最大値を一致としない."""
    state, _ = encounter()
    rule = GambitRule(condition=f"{resource}_equal", threshold=50, definition_id="heal")
    setattr(state.save.run, resource, current)
    setattr(state.save.run, "maxHp" if resource == "hp" else "maxMp", maximum)
    assert condition_matches(rule, state.save, {}) is matches


@pytest.mark.parametrize("target", ["player", "e"])
@pytest.mark.parametrize(("resource", "stat"), [("hp", "maxHp"), ("mp", "maxMp")])
def test_bubble_uses_adjusted_maximum_and_preserves_thresholds(target, resource, stat):
    """本人・敵とも最大値と残量が倍になり、割合判定は同じ境界を使う."""
    state, _ = encounter()
    run, marker = state.save.run, state.save.battle
    if target == "player":
        setattr(run, resource, 50)
        setattr(run, stat, 100)
    else:
        getattr(marker.schedule, f"enemy_{resource}")[target] = 50
        getattr(marker.schedule, f"enemy_max_{resource}")[target] = 100
    skill = parse_skill({
        "id": "bubble",
        "name": "バブル",
        "effect": "buff",
        "stat": stat,
        "amount": 100,
        "amount_mode": "percent",
        "duration_turns": 3,
    })
    add_effect(marker, skill, target)
    marker.turn += 1
    sync_bubble(run, marker)
    for comparison in ("equal", "below", "above"):
        rule = GambitRule(
            condition=f"{resource}_{comparison}",
            threshold=50,
            definition_id="heal",
        )
        assert condition_matches(rule, state.save, {}, target=target) is (
            comparison != "below"
        )
    if target == "player":
        assert (getattr(run, resource), getattr(run, stat)) == (100, 200)
        setattr(run, resource, 99)
    else:
        assert getattr(marker.schedule, f"enemy_{resource}")[target] == 100
        assert getattr(marker.schedule, f"enemy_max_{resource}")[target] == 200
        getattr(marker.schedule, f"enemy_{resource}")[target] = 99
    equal = GambitRule(
        condition=f"{resource}_equal",
        threshold=50,
        definition_id="heal",
    )
    assert not condition_matches(equal, state.save, {}, target=target)


def test_equal_can_be_shared_as_a_condition_variant():
    """一致条件も変型セットで共有でき、保存・再読込で保持される."""
    catalog = GameCatalog.model_validate({
        "trigger_variant_sets": [
            {
                "id": "comparisons",
                "name": "比較",
                "field": "condition",
                "values": [
                    {"id": "same", "name": "同じ", "value": "hp_equal"},
                ],
            },
        ],
        "triggers": [
            {
                "id": "hp",
                "name": "HP",
                "condition": "hp_below",
                "variant_set_id": "comparisons",
            },
        ],
    })
    assert catalog.triggers[0].variant("same").condition == "hp_equal"
    assert GameCatalog.model_validate(catalog.stored_document()) == catalog
