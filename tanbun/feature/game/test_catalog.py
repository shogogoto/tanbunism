"""定義の権限・参照検査と既存アイテムの互換性."""
# ruff: noqa: PLR2004

from typing import get_args

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .catalog import AttackSkill, GameCatalog, IconId, default_catalog
from .economy import EconomyAction, catalog_products, change_economy
from .test_economy import seed_user


@pytest.mark.parametrize(
    "field",
    [
        "castable",
        "requires_specialty",
        "quiz_required",
        "quiz_seconds",
        "wrong_multiplier",
        "power_coefficient",
    ],
)
def test_retired_usage_flags_on_skills_are_rejected(field):
    """スキルに混在した旧利用フラグを、暗黙に移植せず拒否する."""
    data = default_catalog().model_dump()
    data["skills"][0][field] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        GameCatalog.model_validate(data)


def test_missing_usage_lists_do_not_implicitly_grant_skills():
    """利用設定を省略してもスキルの習得権限を自動発行しない."""
    data = default_catalog().model_dump()
    data.pop("player_skills")
    data.pop("specialty_skills")
    catalog = GameCatalog.model_validate(data)
    assert catalog.player_skills == []
    assert catalog.specialty_skills == []


def test_explicit_empty_usage_does_not_grant_direct_access():
    """利用設定なしのスキルも効果本体として保存できる。空設定は再生成しない."""
    data = default_catalog().model_dump()
    data.update(player_skills=[], specialty_skills=[])
    saved = GameCatalog.model_validate(data)
    assert not saved.player_skills
    assert not saved.specialty_skills
    data["player_skills"] = [{"skill_id": "missing"}]
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(data)


def test_missing_shop_does_not_create_listings_from_item_prices():
    """価格の旧保存から商品を自動発行せず、明示した販売設定だけを使う."""
    data = default_catalog().model_dump()
    data.pop("shop")
    for item in data["items"]:
        item["price"] = 10
    assert GameCatalog.model_validate(data).shop == []
    data["shop"] = []
    assert all(
        product.price is None
        for product in catalog_products(GameCatalog.model_validate(data))
    )


def test_shop_references_and_sale_flags_are_validated():
    """販売停止でもアイテム自体の使用は無効にしない."""
    data = default_catalog().model_dump()
    for shop in (
        [{"item_id": "missing", "price": 10}],
        [{"item_id": "healing_potion", "price": -1}],
        [{"item_id": "healing_potion", "price": 1000001}],
        [data["shop"][0], data["shop"][0]],
    ):
        with pytest.raises(ValidationError):
            GameCatalog.model_validate(data | {"shop": shop})
    data["shop"] = [{"item_id": "healing_potion", "price": 42, "enabled": False}]
    products = {p.id: p for p in catalog_products(GameCatalog.model_validate(data))}
    assert products["healing_potion"].price is None
    assert products["healing_potion"].enabled


def test_references_targets_icons_and_duplicate_ids_are_validated():
    """壊れた参照・対象・素材を保存できない."""
    catalog = default_catalog().model_dump()
    for patch in (
        {"items": [{**catalog["items"][0], "skill_id": "missing"}]},
        {"skills": [{**catalog["skills"][0], "target": "self"}]},
        {"skills": [{**catalog["skills"][0], "icon": "unknown"}]},
        {"enemies": [{**catalog["enemies"][0], "attack_skill_id": "heal"}]},
        {"skills": [catalog["skills"][0], catalog["skills"][0]]},
    ):
        with pytest.raises(ValidationError):
            GameCatalog.model_validate(catalog | patch)
    products = {p.id: p for p in catalog_products(default_catalog())}
    assert (products["healing_potion"].amount, products["healing_potion"].price) == (
        20,
        30,
    )
    assert products["bronze_sword"].amount == 3
    assert products["step_potion"].kind == "moves"


def test_all_gallery_icons_can_be_saved_in_skill_definitions():
    """素材一覧の追加アイコンも定義として受け付ける."""
    for icon in get_args(IconId):
        skill = AttackSkill(id="test", name="技", effect="attack", icon=icon)
        assert AttackSkill.model_validate_json(skill.model_dump_json()).icon == icon


def test_imported_animation_ids_are_accepted_but_arbitrary_sources_are_not():
    """素材IDだけを保存し、URLや任意のCSSをカタログに持ち込めない."""
    ids = [
        *(f"oga_slash_{i}" for i in range(1, 6)),
        *(f"oga_spell_{i}" for i in range(1, 11)),
        *(f"oga_effect_{i}" for i in range(1, 6)),
        *(f"oga_nature_{i}" for i in range(1, 5)),
        *(f"oga_arcane_{i}" for i in range(1, 7)),
        *(f"oga_earth_{i}" for i in range(1, 6)),
        *(f"oga_cosmic_{i}" for i in range(1, 6)),
        *(
            f"mochi_status_{effect}"
            for effect in ("curse", "buff", "shield", "heal", "speed")
        ),
    ]
    for animation in ids:
        skill = AttackSkill(
            id="test",
            name="技",
            effect="attack",
            animation=animation,
        )
        assert (
            AttackSkill.model_validate_json(skill.model_dump_json()).animation
            == animation
        )
    with pytest.raises(ValidationError):
        AttackSkill(
            id="test",
            name="技",
            effect="attack",
            animation="https://example.com/evil.webp",
        )


@mark_async_test()
async def test_catalog_requires_admin_and_shares_saved_definitions(ac):
    """管理者だけ変更でき、認証済みユーザーは同じ定義を参照する."""
    user = await aregister("catalog@example.com")
    headers = await aauth_header(user.email)
    path = "/admin/game/catalog"
    data = default_catalog().model_dump()
    assert (await ac.get(path)).status_code == 401
    assert (await ac.put(path, headers=headers, json=data)).status_code == 403
    user.is_superuser = True
    await user.save()
    data["skills"][0]["amount"] = 7
    data["skills"][0]["animation"] = "mochi_status_curse"
    data["enemies"][0]["icon"] = "crawl_dragon"
    data["skills"][0]["icon"] = "crawl_fireball"
    data["items"][0]["icon"] = "crawl_plate_mail"
    data["items"][1]["icon"] = "crawl_visored_helm"
    data["items"][2]["icon"] = "crawl_leather_boots"
    data["shop"].reverse()
    assert (await ac.put(path, headers=headers, json=data)).status_code == 200
    assert (await ac.get("/game/catalog", headers=headers)).json() == data
    data["items"] = []
    assert (await ac.put(path, headers=headers, json=data)).status_code == 422


@mark_async_test()
async def test_custom_items_use_their_saved_skills_and_reject_unknown_ids():
    """固定列挙だった所持品に追加定義を購入・使用できる."""
    from .catalog import (  # noqa: PLC0415
        ItemDefinition,
        ShopListing,
        update_game_catalog,
    )
    from .state import read_state  # noqa: PLC0415

    user = await seed_user()
    catalog = default_catalog()
    catalog.items.append(
        ItemDefinition(id="red_potion", name="赤い薬", skill_id="potion_heal"),
    )
    catalog.shop.append(ShopListing(item_id="red_potion", price=5))
    await update_game_catalog(catalog)
    bought = await change_economy(
        user.uid,
        EconomyAction(revision=0, product_id="red_potion"),
        "buy",
    )
    assert bought.save.economy.gold == 195
    healed = await change_economy(
        user.uid,
        EconomyAction(revision=1, product_id="red_potion"),
        "use",
    )
    assert healed.save.run.hp == 30
    assert healed.save.economy.inventory["red_potion"] == 0
    before = await read_state(user.uid)
    from fastapi import HTTPException  # noqa: PLC0415

    with pytest.raises(HTTPException):
        await change_economy(
            user.uid,
            EconomyAction(revision=2, product_id="missing"),
            "buy",
        )
    assert await read_state(user.uid) == before
