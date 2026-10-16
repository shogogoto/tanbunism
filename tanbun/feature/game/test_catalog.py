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
    data["enemies"][0]["icon"] = "cat"
    assert (await ac.put(path, headers=headers, json=data)).status_code == 200
    assert (await ac.get("/game/catalog", headers=headers)).json() == data
    data["items"] = []
    assert (await ac.put(path, headers=headers, json=data)).status_code == 422


@mark_async_test()
async def test_custom_items_use_their_saved_skills_and_reject_unknown_ids():
    """固定列挙だった所持品に追加定義を購入・使用できる."""
    from .catalog import ItemDefinition, update_game_catalog  # noqa: PLC0415
    from .state import read_state  # noqa: PLC0415

    user = await seed_user()
    catalog = default_catalog()
    catalog.items.append(
        ItemDefinition(id="red_potion", name="赤い薬", skill_id="potion_heal", price=5),
    )
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
