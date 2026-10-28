"""共有セットの組み合わせと派生を、安定した変型IDで保存する."""
# ruff: noqa: PLR2004

from copy import deepcopy

import pytest
from pydantic import ValidationError

from .catalog import GameCatalog, default_catalog
from .economy import buy, catalog_products
from .gambit_access import rule_available
from .gambits import GambitRule, effective_rule
from .inventory import Economy


def document():
    """親で対象を選び、子で割合を選ぶ。全組み合わせは生成しない."""
    return default_catalog().stored_document() | {
        "trigger_variant_sets": [
            {
                "id": "targets",
                "name": "対象",
                "field": "target",
                "values": [
                    {"id": "ally", "name": "味方", "value": "ally"},
                    {"id": "enemy", "name": "敵", "value": "enemy"},
                ],
            },
            {
                "id": "ratios",
                "name": "割合",
                "field": "threshold",
                "values": [
                    {"id": "half", "name": "50%", "value": 50},
                    {"id": "low", "name": "30%", "value": 30},
                ],
            },
        ],
        "triggers": [
            {
                "id": "hp",
                "name": "HP",
                "condition": "hp_below",
                "target": "enemy",
                "threshold": 70,
                "variant_set_ids": ["targets"],
                "variants": [
                    {
                        "id": "friend",
                        "name": "組み合わせ",
                        "variant_selections": [
                            {"set_id": "targets", "value_id": "ally"},
                        ],
                        "variant_set_ids": ["ratios"],
                    },
                    {
                        "id": "friend_half",
                        "name": "組み合わせ",
                        "base_variant_id": "friend",
                        "variant_selections": [
                            {"set_id": "ratios", "value_id": "half"},
                        ],
                    },
                    {
                        "id": "same",
                        "name": "同じ",
                        "base_variant_id": "friend_half",
                        "field": "comparison",
                        "value": "equal",
                    },
                ],
            },
        ],
        "shop": [
            {"trigger_id": "hp", "trigger_variant_id": "friend_half", "price": 10},
        ],
    }


def test_nested_sets_resolve_only_registered_combinations_and_serialize_references():
    """三つの派生だけを保持し、親の変更も候補の改名も自動で伝わる."""
    raw = document()
    catalog = GameCatalog.model_validate(raw)
    root = catalog.triggers[0]
    assert len(root.variants) == 3
    assert (
        root.variant("friend_half").target,
        root.variant("friend_half").threshold,
    ) == ("ally", 50)
    assert root.variants[1].resolved_name == "味方, 50%"
    assert root.variant("same").comparison == "equal"
    assert root.variant("same").condition == "stat"
    stored = catalog.stored_document()
    assert all(
        "resolved_values" not in v and "resolved_name" not in v
        for v in stored["triggers"][0]["variants"]
    )
    assert GameCatalog.model_validate(stored) == catalog
    stored["trigger_variant_sets"][1]["values"][0].update(value=60, name="60%")
    changed = GameCatalog.model_validate(stored)
    assert changed.triggers[0].variant("friend_half").threshold == 60
    assert changed.triggers[0].variants[2].resolved_name == "味方, 60%, 同じ"
    assert raw["trigger_variant_sets"][1]["values"][0]["value"] == 50


def test_multiple_root_sets_and_purchase_use_the_same_stable_variant_id():
    """複数セットを親に結び付けても、購入先は登録した変型のIDになる."""
    raw = document()
    root = raw["triggers"][0]
    root["variant_set_ids"] = ["targets", "ratios"]
    root["variants"] = [
        {
            "id": "friend_half",
            "name": "組み合わせ",
            "variant_selections": [
                {"set_id": "ratios", "value_id": "half"},
                {"set_id": "targets", "value_id": "ally"},
            ],
        },
    ]
    catalog = GameCatalog.model_validate(raw)
    product = next(
        p for p in catalog_products(catalog) if p.id == "gambit:hp:friend_half"
    )
    assert "味方, 50%" in product.name
    economy = Economy(gold=20)
    rule = GambitRule(
        trigger_id="hp",
        trigger_variant_id="friend_half",
        definition_id="heal",
    )
    assert not rule_available(catalog, economy, rule)
    buy(economy, product)
    restored = GameCatalog.model_validate(catalog.stored_document())
    assert rule_available(restored, economy, rule)
    resolved = effective_rule(rule, restored)
    assert (resolved.target, resolved.threshold) == ("ally", 50)


@pytest.mark.parametrize(
    "problem",
    [
        "missing_parent",
        "cycle",
        "missing_value",
        "unattached",
        "conflict",
        "mixed",
        "forged",
    ],
)
def test_invalid_combinations_are_rejected_or_recomputed(problem):
    """循環、参照切れ、項目の競合、派生値の偽装を保存しない."""
    raw = deepcopy(document())
    root = raw["triggers"][0]
    child = root["variants"][1]
    if problem == "missing_parent":
        child["base_variant_id"] = "missing"
    elif problem == "cycle":
        root["variants"][0]["base_variant_id"] = "friend_half"
    elif problem == "missing_value":
        child["variant_selections"][0]["value_id"] = "missing"
    elif problem == "unattached":
        root["variants"][0]["variant_set_ids"] = []
    elif problem == "conflict":
        raw["trigger_variant_sets"].append({
            "id": "again",
            "name": "同項目",
            "field": "target",
            "values": [],
        })
        root["variant_set_ids"].append("again")
    elif problem == "mixed":
        child.update(field="threshold", value=25)
    else:
        child["resolved_values"] = {"target": "self", "threshold": 1}
        assert (
            GameCatalog.model_validate(raw).triggers[0].variant("friend_half").threshold
            == 50
        )
        return
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(raw)


def test_depth_limit_cannot_be_bypassed_by_previously_resolved_parents():
    """親が先に解決済みでも、階層数の上限は同じ."""
    raw = document()
    root = raw["triggers"][0]
    raw["shop"] = []
    root["variants"] = []
    for i in range(17):
        root["variants"].append({
            "id": f"v{i}",
            "name": "派生",
            "field": "threshold",
            "value": 30,
            **({"base_variant_id": f"v{i - 1}"} if i else {}),
        })
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(raw)
