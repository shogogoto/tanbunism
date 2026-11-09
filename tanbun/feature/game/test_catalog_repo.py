"""カタログの保存境界。移行・補完・参照除外と保存形式の契約."""

import json
from unittest.mock import AsyncMock, call

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test

from . import boss_settings_migration, catalog, catalog_repo


@pytest.fixture
def storage(monkeypatch):
    """I/Oだけを差し替え、読み書き・復元の制御は実処理で検証する."""
    query = AsyncMock(return_value=([], []))
    migration = AsyncMock(return_value=None)
    monkeypatch.setattr(catalog_repo.adb, "cypher_query", query)
    monkeypatch.setattr(boss_settings_migration, "migrate_boss_settings", migration)
    return query, migration


@mark_async_test()
async def test_current_document_loads_without_migration_or_write(storage):
    """現行形式は移行せず、未登録の標準行動だけを返却値に補う."""
    query, migration = storage
    document = catalog.default_catalog().stored_document()
    document["skills"] = [
        skill for skill in document["skills"] if skill["id"] != "normal_attack"
    ]
    query.return_value = ([[json.dumps(document)]], [])
    loaded = await catalog_repo.get_game_catalog()
    assert (
        loaded.stored_document()
        == catalog.GameCatalog.model_validate({
            **document,
            "skills": [*document["skills"], catalog.default_normal_attack()],
        }).stored_document()
    )
    query.assert_awaited_once_with(
        "MATCH (s:AdminSettings {key:'game_catalog'}) RETURN s.config LIMIT 1",
    )
    migration.assert_not_awaited()


@mark_async_test()
async def test_missing_document_returns_independent_defaults_without_saving(storage):
    """未保存環境の初期値は共有せず、読み込みだけでは新規保存しない."""
    query, migration = storage
    first = await catalog_repo.get_game_catalog()
    original_name = first.items[0].name
    first.items[0].name = "一時的な編集"
    second = await catalog_repo.get_game_catalog()
    assert second.items[0].name == original_name
    assert second.skills[-1].id == "normal_attack"
    assert first is not second
    migration.assert_has_awaits([call(), call()])
    assert all("RETURN" in args.args[0] for args in query.await_args_list)


@mark_async_test()
async def test_legacy_document_uses_the_document_returned_by_migration(storage):
    """旧形式は最初の取得値ではなく、移行が返した最新の定義を復元する."""
    query, migration = storage
    document = catalog.default_catalog().stored_document()
    legacy = {
        key: value
        for key, value in document.items()
        if key != "boss_multipliers_version"
    }
    query.return_value = ([[json.dumps(legacy)]], [])
    migrated = catalog.default_catalog().stored_document()
    migrated["enemies"][-1]["multipliers"]["hp"] = 6
    migration.return_value = migrated
    loaded = await catalog_repo.get_game_catalog()
    assert (
        loaded.enemies[-1].multipliers.hp
        == migrated["enemies"][-1]["multipliers"]["hp"]
    )
    migration.assert_awaited_once_with()
    query.assert_awaited_once()


@mark_async_test()
async def test_retired_triggers_are_removed_from_every_saved_reference(storage):
    """廃止条件だけを敵・共有セット・販売から除外し、現行ルールを残す."""
    query, _ = storage
    document = catalog.default_catalog().stored_document()
    document["triggers"].append({
        "id": "retired",
        "name": "旧条件",
        "condition": "buff_absent",
    })
    rules = [
        {"trigger_id": "retired", "skill_id": "fire"},
        {"trigger_id": "always", "skill_id": "fire"},
    ]
    document["enemies"][0]["gambits"] = rules
    document["gambit_sets"] = [{"id": "shared", "name": "共通", "rules": rules}]
    document["shop"].extend([
        {"trigger_id": "retired", "price": 10},
        {"trigger_id": "always", "price": 10},
    ])
    query.return_value = ([[json.dumps(document)]], [])
    loaded = await catalog_repo.get_game_catalog()
    assert "retired" not in {trigger.id for trigger in loaded.triggers}
    assert [rule.trigger_id for rule in loaded.enemies[0].gambits] == ["always"]
    assert [rule.trigger_id for rule in loaded.gambit_sets[0].rules] == ["always"]
    assert [listing.product_id for listing in loaded.shop] == [
        *(listing.product_id for listing in catalog.default_catalog().shop),
        "gambit:always",
    ]
    assert [enemy.id for enemy in loaded.enemies] == [
        enemy["id"] for enemy in document["enemies"]
    ]


@mark_async_test()
async def test_invalid_references_are_not_silently_replaced_with_defaults(storage):
    """廃止条件以外の破損は、初期値へのフォールバックで隠さない."""
    query, migration = storage
    document = catalog.default_catalog().stored_document()
    document["items"][0]["skill_id"] = "missing"
    query.return_value = ([[json.dumps(document)]], [])
    with pytest.raises(ValidationError, match="スキルまたは種類が不正"):
        await catalog_repo.get_game_catalog()
    migration.assert_not_awaited()
    query.assert_awaited_once()


@mark_async_test()
async def test_save_rejects_required_item_removal_before_database_operations(storage):
    """既存所持品を壊す削除は、移行やDBへの書き込みより前に拒否する."""
    query, migration = storage
    with pytest.raises(ValueError, match="既存アイテムは削除せず"):
        await catalog_repo.update_game_catalog(catalog.GameCatalog())
    query.assert_not_awaited()
    migration.assert_not_awaited()


@mark_async_test()
async def test_save_migrates_before_writing_compact_reference_document(storage):
    """テンプレート解決値を複製せず保存し、既存移行を先に完了する."""
    query, migration = storage
    document = catalog.default_catalog().stored_document()
    document["skills"][0] = {
        "id": "fire",
        "name": "ファイア",
        "template_ids": ["basic_spell"],
        "mp_cost": 3,
    }
    configured = catalog.GameCatalog.model_validate(document)
    events = []

    def migrate():
        events.append("migration")

    def write(*_args):
        events.append("write")

    migration.side_effect = migrate
    query.side_effect = write
    saved = await catalog_repo.update_game_catalog(configured)
    assert saved is configured
    assert events == ["migration", "write"]
    query.assert_awaited_once_with(
        "MERGE (s:AdminSettings {key:'game_catalog'}) SET s.config=$config",
        {"config": json.dumps(configured.stored_document(), ensure_ascii=False)},
    )
    stored = json.loads(query.await_args.args[1]["config"])
    assert stored["skills"][0]["template_ids"] == ["basic_spell"]
    assert "multiplier" not in stored["skills"][0]


@mark_async_test()
async def test_existing_catalog_functions_delegate_to_the_repository(monkeypatch):
    """既存の呼び出し口は同じ引数・返却値のまま保存モジュールへ渡す."""
    configured = catalog.default_catalog()
    load = AsyncMock(return_value=configured)
    save = AsyncMock(return_value=configured)
    monkeypatch.setattr(catalog_repo, "get_game_catalog", load)
    monkeypatch.setattr(catalog_repo, "update_game_catalog", save)
    assert await catalog.get_game_catalog() is configured
    assert await catalog.update_game_catalog(configured) is configured
    load.assert_awaited_once_with()
    save.assert_awaited_once_with(configured)
