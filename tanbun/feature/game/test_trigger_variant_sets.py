"""トリガーの共有候補は一度定義し、全参照先で同じ値を使う."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test

from .catalog import GameCatalog, default_catalog, get_game_catalog, update_game_catalog
from .economy import buy, catalog_products
from .gambit_access import rule_available
from .gambits import GambitRule, effective_rule
from .inventory import Economy


def shared_document():
    """HPとMPの条件で同じしきい値セットを共有する."""
    return default_catalog().stored_document() | {
        "trigger_variant_sets": [
            {
                "id": "ratios",
                "name": "残量",
                "field": "threshold",
                "values": [
                    {"id": "half", "name": "半分", "value": 50},
                ],
            },
        ],
        "triggers": [
            {
                "id": resource,
                "name": resource,
                "condition": f"{resource}_below",
                "variant_set_id": "ratios",
            }
            for resource in ("hp", "mp")
        ],
        "shop": [{"trigger_id": "hp", "trigger_variant_id": "half", "price": 10}],
    }


def test_shared_candidates_resolve_without_copying_values_into_storage():
    """展開は読み取り時だけ。変更と副名はすべてのトリガーへ伝わる."""
    raw = shared_document()
    catalog = GameCatalog.model_validate(raw)
    assert all(t.variant("half").threshold == 50 for t in catalog.triggers)
    stored = catalog.stored_document()
    assert all(
        "variants" not in t and "variant_field" not in t for t in stored["triggers"]
    )
    assert GameCatalog.model_validate(stored) == catalog
    stored["trigger_variant_sets"][0]["values"][0].update(name="七割", value=70)
    changed = GameCatalog.model_validate(stored)
    assert all(t.variant("half").threshold == 70 for t in changed.triggers)
    assert all(t.variants[0].name == "七割" for t in changed.triggers)
    assert raw["trigger_variant_sets"][0]["values"][0]["value"] == 50


def test_shop_purchase_and_execution_use_shared_candidate_ids():
    """共有元の編集後もショップ商品IDと購入権は変わらない."""
    catalog = GameCatalog.model_validate(shared_document())
    product = next(p for p in catalog_products(catalog) if p.id == "gambit:hp:half")
    economy = Economy(gold=20)
    rule = GambitRule(trigger_id="hp", trigger_variant_id="half", definition_id="heal")
    assert not rule_available(catalog, economy, rule)
    buy(economy, product)
    stored = catalog.stored_document()
    stored["trigger_variant_sets"][0]["values"][0]["value"] = 70
    changed = GameCatalog.model_validate(stored)
    assert rule_available(changed, economy, rule)
    assert effective_rule(rule, changed).threshold == 70


@pytest.mark.parametrize(
    ("field", "value"),
    [("target", "self"), ("state_present", False), ("condition", "hp_above")],
)
def test_sets_support_other_trigger_fields(field, value):
    """しきい値以外の対象・条件・状態有無も共有できる."""
    raw = shared_document()
    raw["trigger_variant_sets"][0].update(field=field)
    raw["trigger_variant_sets"][0]["values"][0]["value"] = value
    catalog = GameCatalog.model_validate(raw)
    assert all(getattr(t.variant("half"), field) == value for t in catalog.triggers)


@pytest.mark.parametrize(
    "problem",
    ["missing", "duplicate", "threshold", "target", "state_skill", "removed_candidate"],
)
def test_invalid_shared_sets_and_dangling_references_are_rejected(problem):
    """利用中のセットや販売中の候補を削除して参照を壊せない."""
    raw = shared_document()
    shared = raw["trigger_variant_sets"][0]
    if problem == "missing":
        raw["trigger_variant_sets"] = []
    elif problem == "duplicate":
        raw["trigger_variant_sets"].append(shared)
    elif problem == "removed_candidate":
        shared["values"] = []
    else:
        shared["field"] = {
            "threshold": "threshold",
            "target": "target",
            "state_skill": "state_skill_id",
        }[problem]
        shared["values"][0]["value"] = 0 if problem == "threshold" else "missing"
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(raw)


@mark_async_test()
async def test_shared_sets_survive_catalog_save_and_reload():
    """保存・再読み込み後も共有参照から変型を復元する."""
    catalog = GameCatalog.model_validate(shared_document())
    await update_game_catalog(catalog)
    restored = await get_game_catalog()
    assert restored == catalog
    assert restored.triggers[1].variant("half").threshold == 50
