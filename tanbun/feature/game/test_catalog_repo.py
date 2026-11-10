"""カタログの保存境界。読み取りの非更新性・検証・保存形式の契約."""

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test

from . import catalog, catalog_repo


@pytest.fixture
def storage(monkeypatch):
    """I/Oだけを差し替え、読み書き・復元の制御は実処理で検証する."""
    query = AsyncMock(return_value=([], []))
    monkeypatch.setattr(catalog_repo.adb, "cypher_query", query)
    return query


@mark_async_test()
async def test_current_document_loads_without_migration_or_write(storage):
    """現行形式は移行せず、未登録の標準行動だけを返却値に補う."""
    query = storage
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


@mark_async_test()
async def test_missing_document_returns_independent_defaults_without_saving(storage):
    """未保存環境の初期値は共有せず、読み込みだけでは新規保存しない."""
    query = storage
    first = await catalog_repo.get_game_catalog()
    original_name = first.items[0].name
    first.items[0].name = "一時的な編集"
    second = await catalog_repo.get_game_catalog()
    assert second.items[0].name == original_name
    assert second.skills[-1].id == "normal_attack"
    assert first is not second
    assert all("RETURN" in args.args[0] for args in query.await_args_list)


@mark_async_test()
async def test_retired_triggers_are_rejected_without_silent_data_repair(storage):
    """廃止した条件を黙って削除・再保存せず、検証エラーにする."""
    document = catalog.default_catalog().stored_document()
    document["triggers"].append({
        "id": "retired",
        "name": "旧条件",
        "condition": "buff_absent",
    })
    storage.return_value = ([[json.dumps(document)]], [])
    with pytest.raises(ValidationError):
        await catalog_repo.get_game_catalog()
    storage.assert_awaited_once()


@mark_async_test()
async def test_invalid_references_are_not_silently_replaced_with_defaults(storage):
    """参照の破損は、初期値へのフォールバックで隠さない."""
    query = storage
    document = catalog.default_catalog().stored_document()
    document["items"][0]["skill_id"] = "missing"
    query.return_value = ([[json.dumps(document)]], [])
    with pytest.raises(ValidationError, match="スキルまたは種類が不正"):
        await catalog_repo.get_game_catalog()
    query.assert_awaited_once()


@mark_async_test()
async def test_save_rejects_required_item_removal_before_database_operations(storage):
    """既存所持品を壊す削除は、DBへの書き込みより前に拒否する."""
    query = storage
    with pytest.raises(ValueError, match="既存アイテムは削除せず"):
        await catalog_repo.update_game_catalog(catalog.GameCatalog())
    query.assert_not_awaited()


@mark_async_test()
async def test_save_writes_only_the_compact_reference_document(storage):
    """テンプレート解決値を複製せず保存し、他の設定へ触れない."""
    query = storage
    document = catalog.default_catalog().stored_document()
    document["skills"][0] = {
        "id": "fire",
        "name": "ファイア",
        "template_ids": ["basic_spell"],
        "mp_cost": 3,
    }
    configured = catalog.GameCatalog.model_validate(document)
    saved = await catalog_repo.update_game_catalog(configured)
    assert saved is configured
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
