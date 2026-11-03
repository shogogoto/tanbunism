"""共有ガンビットは複数の敵から参照され、遭遇時に固定される."""

import pytest
from pydantic import ValidationError

from .catalog import EnemyGambitRule, GambitSet, GameCatalog, default_catalog
from .enemy_gambits import enemy_battle_rules


def configured():
    """二体から同じ行動セットを参照する."""
    catalog = default_catalog()
    catalog.gambit_sets = [
        GambitSet(
            id="mage",
            name="魔法使い",
            rules=[EnemyGambitRule(trigger_id="always", skill_id="fire")],
        ),
    ]
    for enemy in catalog.enemies[:2]:
        enemy.gambit_set_id = "mage"
    return GameCatalog.model_validate(catalog.model_dump())


def test_shared_rules_are_resolved_and_frozen_for_each_enemy():
    """定義の更新は新しい遭遇に反映し、進行中の遭遇には遡及しない."""
    catalog = configured()
    frozen = [enemy_battle_rules(enemy, catalog) for enemy in catalog.enemies[:2]]
    assert all(rules[0].skill.id == "fire" for rules in frozen)
    catalog.gambit_sets[0].rules[0].skill_id = "heal"
    assert all(
        enemy_battle_rules(enemy, catalog)[0].skill.id == "heal"
        for enemy in catalog.enemies[:2]
    )
    assert all(rules[0].skill.id == "fire" for rules in frozen)
    assert (
        GameCatalog.model_validate_json(catalog.model_dump_json()).gambit_sets
        == catalog.gambit_sets
    )


@pytest.mark.parametrize(
    "invalid",
    ["missing_set", "missing_trigger", "missing_skill", "duplicate"],
)
def test_set_references_are_validated_even_when_not_equipped(invalid):
    """未使用セットも含めて参照と一意性を確認する."""
    data = configured().model_dump()
    if invalid == "missing_set":
        data["gambit_sets"] = []
    elif invalid == "missing_trigger":
        data["gambit_sets"][0]["rules"][0]["trigger_id"] = "missing"
    elif invalid == "missing_skill":
        data["gambit_sets"][0]["rules"][0]["skill_id"] = "missing"
    else:
        data["gambit_sets"].append(data["gambit_sets"][0])
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(data)
