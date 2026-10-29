"""ユーザーは所有する条件と行動を選択し、条件自体を定義しない."""
# ruff: noqa: PLR2004

import pytest
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .catalog import TriggerVariant, default_catalog, update_game_catalog
from .specialty_types import Specialty
from .state import read_state


async def configured(unlocked=(), inventory=None):
    """購入処理以外のテストでは取得済みの所有権だけをfixtureとして保存する."""
    user = await aregister("ownership@example.com")
    headers = await aauth_header(user.email)
    state = await read_state(user.uid)
    state.save.economy.unlocked_triggers = list(unlocked)
    state.save.economy.inventory = inventory or {}
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state",
        {"uid": user.uid, "state": state.model_dump_json()},
    )
    return user, headers, state


@pytest.mark.parametrize("case", ["unsold", "inline", "variant", "missing", "disabled"])
@mark_async_test()
async def test_unowned_or_invalid_triggers_cannot_be_saved(ac, case):
    """販売未設定・親だけ入手・条件の直接記述による迂回を拒否する."""
    owned = ["hp_below"] if case in {"variant", "disabled"} else ["missing"]
    user, headers, before = await configured(owned)
    catalog = default_catalog()
    trigger = next(t for t in catalog.triggers if t.id == "hp_below")
    trigger.variants = [TriggerVariant(id="half", name="半分", value=50)]
    if case == "disabled":
        trigger.enabled = False
    await update_game_catalog(catalog)
    rule = {"trigger_id": "hp_below", "definition_id": "heal"}
    if case == "inline":
        rule = {"condition": "always", "definition_id": "heal"}
    if case == "variant":
        rule["trigger_variant_id"] = "half"
    if case == "missing":
        rule["trigger_id"] = "missing"
    response = await ac.put(
        "/game/gambits",
        headers=headers,
        json={"revision": 0, "rules": [rule]},
    )
    assert response.status_code == 422
    assert await read_state(user.uid) == before


@pytest.mark.parametrize("target", [None, "ally"])
@mark_async_test()
async def test_admin_condition_replaces_all_client_overrides(ac, target):
    """対象未設定の管理定義でもユーザーが対象や値を注入できない."""
    _user, headers, _ = await configured(["hp_below:half"])
    catalog = default_catalog()
    trigger = next(t for t in catalog.triggers if t.id == "hp_below")
    trigger.target = target
    trigger.variants = [TriggerVariant(id="half", name="半分", value=50)]
    await update_game_catalog(catalog)
    response = await ac.put(
        "/game/gambits",
        headers=headers,
        json={
            "revision": 0,
            "rules": [
                {
                    "trigger_id": "hp_below",
                    "trigger_variant_id": "half",
                    "definition_id": "heal",
                    "condition": "always",
                    "threshold": 100,
                    "target": "enemy",
                    "comparison": "highest",
                    "state_present": False,
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    rule = response.json()["save"]["gambits"][0]
    assert rule["condition"] == "hp_below"
    assert rule["threshold"] == 50
    assert rule["target"] == target
    assert rule["comparison"] == "below"
    assert rule["state_present"] is True


@pytest.mark.parametrize(
    ("action", "identity"),
    [
        ("item", "healing_potion"),
        ("skill", "potion_heal"),
        ("specialty", "specialty_missing"),
    ],
)
@mark_async_test()
async def test_unowned_actions_are_rejected_when_saving(ac, action, identity):
    """実行時に飛ばすだけでなく、保存する時点でも未所持の行動を拒否する."""
    user, headers, before = await configured(["always"])
    response = await ac.put(
        "/game/gambits",
        headers=headers,
        json={
            "revision": 0,
            "rules": [
                {
                    "trigger_id": "always",
                    "action": action,
                    "definition_id": identity,
                },
            ],
        },
    )
    assert response.status_code == 422
    assert await read_state(user.uid) == before


@mark_async_test()
async def test_owned_item_can_be_combined_and_saved(ac):
    """通常アイテムが持つスキルは、所持品を消費する行動として選択できる."""
    _user, headers, _ = await configured(["always"], {"healing_potion": 1})
    response = await ac.put(
        "/game/gambits",
        headers=headers,
        json={
            "revision": 0,
            "rules": [
                {
                    "trigger_id": "always",
                    "action": "item",
                    "definition_id": "healing_potion",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["save"]["gambits"][0]["action"] == "item"


@pytest.mark.parametrize(
    ("equipped", "enabled", "accessible", "expected"),
    [
        (True, True, True, 200),
        (False, True, True, 422),
        (True, False, True, 422),
        (True, True, False, 422),
    ],
)
@mark_async_test()
async def test_specialty_access_requires_equipment_and_enabled_skill(
    ac,
    equipped,
    enabled,
    accessible,
    expected,
):
    """保持しているだけの専門分野や、利用許可を失ったスキルは選択できない."""
    user, headers, state = await configured(["always"])
    state.save.specialties = [
        Specialty(
            id="specialty_test",
            name="専門分野",
            resource_ids=["book"],
            skill_id="specialty_burst",
            equipped=equipped,
        ),
    ]
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state",
        {"uid": user.uid, "state": state.model_dump_json()},
    )
    catalog = default_catalog()
    next(s for s in catalog.skills if s.id == "specialty_burst").enabled = enabled
    catalog.specialty_skills[0].enabled = accessible
    await update_game_catalog(catalog)
    response = await ac.put(
        "/game/gambits",
        headers=headers,
        json={
            "revision": 0,
            "rules": [
                {
                    "trigger_id": "always",
                    "action": "specialty",
                    "definition_id": "specialty_test",
                },
            ],
        },
    )
    assert response.status_code == expected, response.text
