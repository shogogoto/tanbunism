"""定義の権限・参照検査と既存アイテムの互換性."""
# ruff: noqa: PLR2004

from typing import get_args

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .catalog import GameCatalog, IconId, SkillDefinition, default_catalog
from .economy import EconomyAction, catalog_products, change_economy
from .test_economy import seed_user


def test_legacy_skill_flags_migrate_to_consumers_without_remaining_on_skills():
    """旧カタログを読み直すと、利用制限・出題設定は独立した参照へ移る."""
    old = default_catalog().model_dump()
    old.pop("player_skills")
    old.pop("specialty_skills")
    for skill in old["skills"]:
        if skill["id"] in {"potion_heal", "ether_heal"}:
            skill["castable"] = False
        if skill["id"] == "specialty_burst":
            skill.update(
                requires_specialty=True,
                quiz_required=True,
                quiz_seconds=42,
                wrong_multiplier=0.5,
                power_coefficient=2,
            )
    migrated = GameCatalog.model_validate(old)
    assert {u.skill_id for u in migrated.player_skills} == {"fire", "fire_all", "heal"}
    use = migrated.specialty_skills[0]
    assert (
        use.skill_id,
        use.quiz_seconds,
        use.wrong_multiplier,
        use.power_coefficient,
    ) == ("specialty_burst", 42, 0.5, 2)
    for skill in migrated.model_dump()["skills"]:
        assert (
            not {
                "castable",
                "requires_specialty",
                "quiz_required",
                "quiz_seconds",
                "wrong_multiplier",
                "power_coefficient",
            }
            & skill.keys()
        )
    assert GameCatalog.model_validate_json(migrated.model_dump_json()) == migrated


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


def test_legacy_prices_migrate_to_shop_and_explicit_empty_shop_stays_empty():
    """非売品を販売に変えず、価格0も含めて旧カタログを移行する."""
    data = default_catalog().model_dump()
    data.pop("shop")
    for index, item in enumerate(data["items"]):
        item["price"] = index * 10 if index < 2 else None
    migrated = GameCatalog.model_validate(data)
    assert [p.price for p in migrated.shop] == [0, 10]
    assert all("price" not in item for item in migrated.model_dump()["items"])
    assert GameCatalog.model_validate_json(migrated.model_dump_json()) == migrated
    data["shop"] = []
    assert all(
        p.price is None for p in catalog_products(GameCatalog.model_validate(data))
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
        skill = SkillDefinition(id="test", name="技", effect="attack", icon=icon)
        assert SkillDefinition.model_validate_json(skill.model_dump_json()).icon == icon


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
        skill = SkillDefinition(
            id="test",
            name="技",
            effect="attack",
            animation=animation,
        )
        assert (
            SkillDefinition.model_validate_json(skill.model_dump_json()).animation
            == animation
        )
    with pytest.raises(ValidationError):
        SkillDefinition(
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
    data["items"][0]["icon"] = "crawl_red_potion"
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
