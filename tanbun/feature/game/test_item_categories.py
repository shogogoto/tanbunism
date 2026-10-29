"""表示カテゴリは装備・消耗の挙動とは独立して保存する."""

import pytest
from pydantic import ValidationError

from .catalog import GameCatalog, ItemDefinition, default_catalog
from .dungeon_keys import key_products
from .economy import catalog_products
from .inventory import DungeonKey
from .item_categories import ItemCategory
from .state import GameSave


def test_existing_items_receive_categories_once():
    """旧データを補完し、JSON往復後もその分類を維持する."""
    raw = default_catalog().stored_document()
    for item in raw["items"]:
        item.pop("category", None)
    catalog = GameCatalog.model_validate(raw)
    categories = {item.id: item.category for item in catalog.items}
    assert categories["bronze_sword"] == ItemCategory.WEAPON
    assert categories["healing_potion"] == ItemCategory.RECOVERY
    assert categories["ether"] == ItemCategory.RECOVERY
    assert categories["step_potion"] == ItemCategory.RECOVERY
    assert GameCatalog.model_validate(catalog.stored_document()).items == catalog.items


@pytest.mark.parametrize("category", list(ItemCategory))
def test_explicit_category_does_not_change_item_behavior(category):
    """回復効果を持つ武器なども表示先を明示でき、効果から再分類しない."""
    raw = default_catalog().stored_document()
    raw["items"][0]["category"] = category
    catalog = GameCatalog.model_validate(raw)
    item = catalog.items[0]
    assert item.category == category
    product = next(p for p in catalog_products(catalog) if p.id == item.id)
    assert product.category == category
    assert product.kind == "weapon"
    assert product.max_uses == item.max_uses
    assert GameCatalog.model_validate(catalog.stored_document()).items == catalog.items


def test_invalid_category_is_rejected():
    """タイプミスで分類が黙って消えない."""
    with pytest.raises(ValidationError):
        ItemDefinition(id="item", name="品", skill_id="heal", category="invalid")


def test_resource_keys_are_grouped_as_unlock_keys():
    """動的に発行される鍵も通常所持品と同じ分類を返す."""
    save = GameSave()
    save.economy.dungeon_keys["@dungeon:book"] = DungeonKey(
        resource_id="book",
        name="本",
    )
    save.economy.inventory["@dungeon:book"] = 1
    assert key_products(save)[0].category == ItemCategory.UNLOCK_KEY
