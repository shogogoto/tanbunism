"""トリガーは二重定義せず商品化し、購入で永久に利用可能になる。."""
# ruff: noqa: PLR2004

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tanbun.conftest import mark_async_test

from .catalog import (
    GambitShopListing,
    GameCatalog,
    ShopListing,
    TriggerVariant,
    default_catalog,
    update_game_catalog,
)
from .economy import EconomyAction, buy, catalog_products, change_economy
from .gambit_access import rule_available, trigger_available
from .gambits import GambitRule, choose_action
from .inventory import Economy
from .state import read_state
from .test_economy import seed_user
from .test_gambits import battle_state


def configured_catalog():
    """通常品と同じショップ設定で、HP条件を販売する。."""
    catalog = default_catalog()
    catalog.shop.append(GambitShopListing(trigger_id="hp_below", price=75))
    return catalog


def test_all_defined_triggers_automatically_become_products_without_item_copies():
    """販売登録前から全トリガーが商品候補として一度ずつ現れる。."""
    catalog = configured_catalog()
    products = [p for p in catalog_products(catalog) if p.kind == "gambit"]
    assert {p.trigger_id for p in products} == {t.id for t in catalog.triggers}
    assert len(products) == len(catalog.triggers)
    hp = next(p for p in products if p.trigger_id == "hp_below")
    assert hp.id == "gambit:hp_below"
    assert hp.price == 75
    assert next(p for p in products if p.trigger_id == "always").price is None
    assert all(i.id != hp.id for i in catalog.items)
    catalog.triggers[1].name = "更新した条件"
    assert (
        next(p for p in catalog_products(catalog) if p.id == hp.id).name
        == "更新した条件"
    )


def test_purchase_unlocks_once_without_inventory_or_durability():
    """購入で直接解放し、再購入や消耗品としての使用を要さない。."""
    catalog = configured_catalog()
    product = next(
        p
        for p in catalog_products(catalog)
        if p.kind == "gambit" and p.price is not None
    )
    economy = Economy(gold=100)
    assert not trigger_available(catalog, economy, "hp_below")
    buy(economy, product)
    assert economy.gold == 25
    assert economy.unlocked_triggers == ["hp_below"]
    assert economy.inventory == {}
    assert economy.remaining_uses == {}
    assert trigger_available(catalog, economy, "hp_below")
    with pytest.raises(HTTPException, match="解放済み"):
        buy(economy, product)
    assert economy.gold == 25


def test_variants_are_independently_priced_purchased_and_unlocked():
    """基本と各変型を別商品にし、一方の購入で他方を解放しない."""
    catalog = configured_catalog()
    trigger = next(t for t in catalog.triggers if t.id == "hp_below")
    trigger.variants = [TriggerVariant(id="half", name="半分", value=50)]
    catalog.shop.append(
        GambitShopListing(
            trigger_id=trigger.id,
            trigger_variant_id="half",
            price=40,
        ),
    )
    catalog = GameCatalog.model_validate(catalog.stored_document())
    products = {p.id: p for p in catalog_products(catalog)}
    base, variant = products["gambit:hp_below"], products["gambit:hp_below:half"]
    assert (base.price, variant.price) == (75, 40)
    assert variant.name == f"{trigger.name}（半分）"  # noqa: RUF001
    economy = Economy(gold=200)
    rule = GambitRule(
        trigger_id=trigger.id,
        trigger_variant_id="half",
        definition_id="heal",
    )
    assert not rule_available(catalog, economy, rule)
    buy(economy, variant)
    assert economy.gold == 160
    assert economy.unlocked_triggers == ["hp_below:half"]
    assert rule_available(catalog, economy, rule)
    assert not trigger_available(catalog, economy, trigger.id)
    with pytest.raises(HTTPException, match="解放済み"):
        buy(economy, variant)
    buy(economy, base)
    assert economy.unlocked_triggers == ["hp_below:half", "hp_below"]


def test_unsold_variants_require_their_own_ownership():
    """基本条件を持っていても、未入手の変型は使えない."""
    catalog = configured_catalog()
    trigger = next(t for t in catalog.triggers if t.id == "hp_below")
    trigger.variants = [TriggerVariant(id="half", name="半分", value=50)]
    economy = Economy()
    assert not trigger_available(catalog, economy, trigger.id, "half")
    economy.unlocked_triggers.append(trigger.id)
    assert not trigger_available(catalog, economy, trigger.id, "half")
    economy.unlocked_triggers.append(f"{trigger.id}:half")
    assert trigger_available(catalog, economy, trigger.id, "half")


def test_unpurchased_trigger_cannot_execute_or_be_bypassed_with_inline_condition():
    """購入前は実行不可で、直接条件を送っても迂回できない。."""
    catalog = configured_catalog()
    state = battle_state()
    state.save.economy.unlocked_triggers = []
    rule = GambitRule(trigger_id="hp_below", definition_id="heal")
    state.save.gambits = [rule]
    assert choose_action(state.save, catalog) is None
    assert not rule_available(
        catalog,
        state.save.economy,
        GambitRule(condition="hp_below", definition_id="heal"),
    )
    state.save.economy.unlocked_triggers.append("hp_below")
    assert choose_action(state.save, catalog).skill.id == "heal"
    assert not trigger_available(catalog, state.save.economy, "always")


def test_unsold_triggers_and_inline_conditions_are_not_free():
    """ショップ未掲載でも所有が必要。存在しない・無効な所有IDも使えない."""
    catalog = default_catalog()
    economy = Economy()
    assert not trigger_available(catalog, economy, "always")
    assert not rule_available(catalog, economy, GambitRule(definition_id="fire"))
    economy.unlocked_triggers = ["always", "missing"]
    assert trigger_available(catalog, economy, "always")
    assert not trigger_available(catalog, economy, "missing")
    next(t for t in catalog.triggers if t.id == "always").enabled = False
    assert not trigger_available(catalog, economy, "always")


def test_normal_items_and_trigger_products_can_share_definition_ids():
    """通常品とトリガーを名前空間で区別して取り扱う。."""
    raw = default_catalog().stored_document()
    raw["triggers"] = [{"id": "healing_potion", "name": "条件", "condition": "always"}]
    raw["shop"] = [
        {"item_id": "healing_potion", "price": 30},
        {"trigger_id": "healing_potion", "price": 75},
    ]
    catalog = GameCatalog.model_validate(raw)
    ids = {p.id for p in catalog_products(catalog)}
    assert {"healing_potion", "gambit:healing_potion"} <= ids
    assert isinstance(catalog.shop[0], ShopListing)
    assert isinstance(catalog.shop[1], GambitShopListing)
    assert GameCatalog.model_validate(catalog.stored_document()) == catalog


@pytest.mark.parametrize(
    "listing",
    [
        {"trigger_id": "missing", "price": 10},
        {"trigger_id": "always", "trigger_variant_id": "missing", "price": 10},
        {"trigger_id": "always", "price": -1},
        {"trigger_id": "always", "item_id": "healing_potion", "price": 10},
    ],
)
def test_invalid_trigger_sale_settings_are_rejected(listing):
    """存在しない参照・負の価格・曖昧な商品参照を保存しない。."""
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(
            default_catalog().stored_document() | {"shop": [listing]},
        )


@pytest.mark.parametrize("variant_id", [None, "half"])
@mark_async_test()
async def test_purchase_is_revision_protected_and_survives_reload(variant_id):
    """同じ購入の再送で二重決済せず、解放をサーバーへ保存する。."""
    user = await seed_user()
    catalog = configured_catalog()
    if variant_id:
        trigger = next(t for t in catalog.triggers if t.id == "hp_below")
        trigger.variants = [TriggerVariant(id="half", name="半分", value=50)]
        catalog.shop.append(
            GambitShopListing(
                trigger_id=trigger.id,
                trigger_variant_id=variant_id,
                price=75,
            ),
        )
    await update_game_catalog(catalog)
    unlock_id = "hp_below:half" if variant_id else "hp_below"
    request = EconomyAction(revision=0, product_id=f"gambit:{unlock_id}")
    bought = await change_economy(user.uid, request, "buy")
    assert bought.save.economy.gold == 125
    assert bought.save.economy.unlocked_triggers == [unlock_id]
    with pytest.raises(HTTPException) as stale:
        await change_economy(user.uid, request, "buy")
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException, match="解放済み"):
        await change_economy(
            user.uid,
            request.model_copy(update={"revision": 1}),
            "buy",
        )
    restored = await read_state(user.uid)
    assert restored.save.economy.gold == 125
    assert restored.save.economy.unlocked_triggers == [unlock_id]
