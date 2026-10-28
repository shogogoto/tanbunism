"""管理設定全体の交換と、非保存プレビュー・一括反映の検証."""
# ruff: noqa: PLR2004

from copy import deepcopy

import pytest
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .balance import GameBalance, update_game_balance
from .catalog import default_catalog
from .settings import BattleSettings
from .settings_files import (
    SettingsFileSave,
    SettingsSnapshot,
    merge_settings,
    read_settings,
    save_settings_file,
    settings_preview,
)


def snapshot():
    """変更のない標準設定のスナップショット."""
    return SettingsSnapshot(
        catalog=default_catalog(),
        balance=GameBalance(),
        battle=BattleSettings(),
    )


def patch(**sections):
    """一部だけを含む交換ファイル."""
    return {"format": "tanbunism.game-settings", "version": 1, **sections}


def test_round_trip_and_partial_settings_preserve_unspecified_sections():
    """往復しても差分は発生せず、省略値は現行設定を引き継ぐ."""
    current = snapshot()
    assert settings_preview(current, current.document())["changes"] == []
    merged = merge_settings(current, patch(balance={"base_hp": 77}))
    assert merged.balance.base_hp == 77
    assert merged.balance.base_mp == current.balance.base_mp
    assert merged.catalog == current.catalog
    assert merged.battle == current.battle
    assert current.balance.base_hp != 77


def test_cross_section_references_order_and_compact_variants():
    """新スキル・アイテム・トリガー・敵・販売を一度に取り込める."""
    current = snapshot()
    source = current.document()
    skill = source["catalog"]["skills"][0] | {"id": "new_spell", "name": "新魔法"}
    definition = {
        "skills": [skill],
        "items": [{"id": "wand", "name": "杖", "skill_id": "new_spell"}],
        "triggers": [
            {
                "id": "new_trigger",
                "name": "いつでも",
                "condition": "always",
                "target": "enemy",
                "variants": [
                    {
                        "id": "friend",
                        "name": "味方",
                        "field": "target",
                        "value": "ally",
                    },
                ],
            },
        ],
        "enemies": [
            {
                "id": "mage",
                "name": "魔法使い",
                "gambits": [{"trigger_id": "new_trigger", "skill_id": "new_spell"}],
            },
        ],
        "shop": [
            {"item_id": "wand", "price": 20},
            {"trigger_id": "new_trigger", "trigger_variant_id": "friend", "price": 10},
        ],
        "player_skills": [{"skill_id": "new_spell", "enabled": True}],
        "specialty_skills": [{"skill_id": "new_spell", "enabled": True}],
    }
    preview = settings_preview(current, patch(catalog=definition))
    catalog = preview["document"]["catalog"]
    assert catalog["skills"][0]["id"] == "new_spell"
    assert len(catalog["skills"]) == len(current.catalog.skills) + 1
    assert catalog["enemies"][0]["gambits"][0]["skill_id"] == "new_spell"
    assert "resolved_values" not in catalog["triggers"][0]["variants"][0]
    assert preview["backup"] == source
    assert current.document() == source


@pytest.mark.parametrize(
    "changes",
    [
        {"catalog": {"inventory": []}},
        {"balance": {"misspelled": 2}},
        {"battle": {"base_seconds": 0}},
        {"save": {"gold": 999}},
        {"catalog": None},
        {"catalog": {"skills": None}},
        {"catalog": {"skills": [{"id": []}]}},
        {"catalog": {"shop": [{"item_id": {}}]}},
        {
            "catalog": {
                "items": [{"id": "missing", "name": "不正", "skill_id": "missing"}],
            },
        },
        {"catalog": {"skills": [{"id": "same"}, {"id": "same"}]}},
        {"version": 99},
        {"format": "other"},
    ],
)
def test_invalid_files_fail_before_changing_any_setting(changes):
    """未知キー、値、重複や参照切れを黙って破棄しない."""
    current = snapshot()
    before = deepcopy(current.document())
    with pytest.raises(ValueError, match=r".+"):
        merge_settings(current, patch(**changes))
    assert current.document() == before


def test_old_skill_files_use_the_same_preview_without_replacing_consumers():
    """以前の書き出しファイルも共通の取り込み口で利用できる."""
    current = snapshot()
    skill = current.catalog.skills[0].model_dump() | {"name": "変更後"}
    result = merge_settings(
        current,
        {"format": "tanbunism.skills", "version": 6, "skills": [skill]},
    )
    assert result.catalog.skills[0].name == "変更後"
    assert result.catalog.items == current.catalog.items


@mark_async_test()
async def test_admin_preview_apply_and_conflict_leave_other_data_untouched(ac):
    """権限、非保存プレビュー、全設定の反映と競合拒否をHTTPで確認する."""
    path = "/admin/game/settings-file"
    assert (await ac.get(path)).status_code == 401
    user = await aregister("game-settings-file@example.com")
    headers = await aauth_header(user.email)
    assert (await ac.get(path, headers=headers)).status_code == 403
    user.is_superuser = True
    await user.save()
    before = (await ac.get(path, headers=headers)).json()
    document = patch(balance={"base_hp": 77}, battle={"base_seconds": 60})
    response = await ac.post(
        path + "/preview",
        json={"document": document},
        headers=headers,
    )
    assert response.status_code == 200
    preview = response.json()
    assert (await ac.get(path, headers=headers)).json() == before
    saved = await ac.put(
        path,
        json={"document": preview["document"], "revision": preview["revision"]},
        headers=headers,
    )
    assert saved.status_code == 200
    assert saved.json()["balance"]["base_hp"] == 77
    assert saved.json()["battle"]["base_seconds"] == 60
    assert (
        await ac.put(
            path,
            json={"document": preview["document"], "revision": preview["revision"]},
            headers=headers,
        )
    ).status_code == 409
    invalid = patch(
        balance={"base_hp": 90},
        catalog={"items": [{"id": "bad", "name": "bad", "skill_id": "missing"}]},
    )
    assert (
        await ac.post(path + "/preview", json={"document": invalid}, headers=headers)
    ).status_code == 422
    assert (await ac.get(path, headers=headers)).json() == saved.json()


@mark_async_test()
async def test_failure_rolls_back_all_settings(monkeypatch):
    """途中の保存が失敗しても一部の設定だけを反映しない."""
    from . import settings_files  # noqa: PLC0415

    await update_game_balance(GameBalance(base_hp=51))
    before = await read_settings()
    document = patch(balance={"base_hp": 77}, battle={"base_seconds": 60})

    async def fail(_settings):  # noqa: RUF029
        msg = "storage failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(settings_files, "update_battle_settings", fail)
    with pytest.raises(RuntimeError, match="storage failed"):
        await save_settings_file(
            SettingsFileSave(document=document, revision=before.revision()),
        )
    assert (await read_settings()).document() == before.document()
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings) WHERE s.settings_file_lock IS NOT NULL RETURN s",
    )
    assert not rows
