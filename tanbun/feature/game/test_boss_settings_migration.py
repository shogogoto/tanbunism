"""共通ボス補正を敵の個性へ統合し、読み込み・再保存で重ね掛けしない。."""
# ruff: noqa: PLR2004

import asyncio
import json

import pytest
from neomodel import adb

from tanbun.conftest import mark_async_test

from .balance import GameBalance, get_game_balance, update_game_balance
from .boss_settings_migration import consolidate_boss_multipliers
from .catalog import GameCatalog, default_catalog, get_game_catalog, update_game_catalog


def legacy_catalog():
    """旧形式は全ボス共通補正を含まない、相対的な敵の倍率を保持する。."""
    raw = default_catalog().stored_document()
    raw.pop("boss_multipliers_version")
    boss = next(e for e in raw["enemies"] if e["boss"])
    boss["multipliers"] = {"hp": 2, "attack": 1.5, "mp": 0.1, "defense": 20}
    raw["enemies"][0]["multipliers"]["hp"] = 1.7
    return raw


async def put_settings(key, config):
    """現在のモデルを通さず、旧保存形式をテストDBへ配置する。."""
    await adb.cypher_query(
        "MERGE (s:AdminSettings {key:$key}) SET s.config=$config",
        {"key": key, "config": json.dumps(config)},
    )


def test_merged_factors_preserve_extreme_values_and_fixed_speed():
    """旧二段階で可能だった上限・下限も失わず、固定速さには重ねない。."""
    raw = legacy_catalog()
    boss = next(e for e in raw["enemies"] if e["boss"])
    boss["speed"] = 4
    boss["multipliers"]["speed"] = 0.5
    balance = {
        "enemy_hp": 20,
        "boss_hp_multiplier": 3,
        "boss_attack_multiplier": 2,
        "boss_mp_multiplier": 0.1,
        "boss_defense_multiplier": 20,
        "boss_speed_multiplier": 8,
    }
    consolidate_boss_multipliers(raw, balance)
    values = GameCatalog.model_validate(raw).enemies[-1].multipliers
    assert values.hp == 6
    assert values.attack == 3
    assert values.special_attack == 2
    assert values.mp == pytest.approx(0.01)
    assert values.defense == 400
    assert values.speed == pytest.approx(0.5)
    assert balance == {"enemy_hp": 20}
    snapshot = json.dumps(raw)
    consolidate_boss_multipliers(raw, balance)
    assert json.dumps(raw) == snapshot


@mark_async_test()
async def test_unconfigured_reads_do_not_create_settings_and_default_boss_is_explicit():
    """新規環境では設定を書かず、既定ボスの強さも敵定義だけに持つ。."""
    assert isinstance(await get_game_balance(), GameBalance)
    catalog = await get_game_catalog()
    boss = next(e for e in catalog.enemies if e.boss)
    assert boss.multipliers.hp == 3
    assert boss.multipliers.attack == pytest.approx(1.25)
    rows, _ = await adb.cypher_query("MATCH (s:AdminSettings) RETURN count(s)")
    assert rows[0][0] == 0


@mark_async_test()
async def test_old_settings_are_consolidated_once_even_for_concurrent_reads():
    """並列の初回読み込みも二重に倍率を掛けず、再保存後も同じになる。."""
    await put_settings("game_catalog", legacy_catalog())
    await put_settings(
        "game-balance",
        {"enemy_hp": 42, "boss_hp_multiplier": 3, "boss_attack_multiplier": 2},
    )
    first, _, second = await asyncio.gather(
        get_game_catalog(),
        get_game_balance(),
        get_game_catalog(),
    )
    for catalog in (first, second):
        boss = next(e for e in catalog.enemies if e.boss)
        assert boss.multipliers.hp == 6
        assert boss.multipliers.attack == 3
        assert boss.multipliers.special_attack == 2
        assert catalog.enemies[0].multipliers.hp == pytest.approx(1.7)
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game-balance'}) RETURN s.config",
    )
    assert json.loads(rows[0][0]) == {"enemy_hp": 42}
    await update_game_catalog(first)
    await update_game_balance(GameBalance(enemy_hp=43))
    assert (await get_game_catalog()).enemies[-1].multipliers.hp == 6


@mark_async_test()
async def test_balance_without_catalog_transfers_values_into_default_boss():
    """敵一覧未保存でも、旧共通設定の強さを既定ボスへ移す。."""
    await put_settings(
        "game-balance",
        {"boss_hp_multiplier": 5, "boss_attack_multiplier": 2},
    )
    await get_game_balance()
    boss = next(e for e in (await get_game_catalog()).enemies if e.boss)
    assert boss.multipliers.hp == 5
    assert boss.multipliers.attack == 2
    assert boss.multipliers.special_attack == 2


@mark_async_test()
async def test_old_catalog_without_balance_uses_previous_common_defaults_once():
    """共通設定未保存なら旧既定値を移し、非ボスには一切掛けない。."""
    await put_settings("game_catalog", legacy_catalog())
    catalog = await get_game_catalog()
    boss = next(e for e in catalog.enemies if e.boss)
    assert boss.multipliers.hp == 6
    assert boss.multipliers.attack == pytest.approx(1.875)
    assert catalog.enemies[0].multipliers.hp == pytest.approx(1.7)
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game-balance'}) RETURN s",
    )
    assert rows == []


@mark_async_test()
async def test_failed_migration_does_not_partially_replace_settings(monkeypatch):
    """移行失敗は全てロールバックし、再試行で元の倍率を一度だけ移す。."""
    await put_settings("game_catalog", legacy_catalog())
    await put_settings("game-balance", {"boss_hp_multiplier": 3})
    original = adb.cypher_query

    async def fail_write(query, *args, **kwargs):
        if "UNWIND $documents" in query:
            msg = "migration failed"
            raise RuntimeError(msg)
        return await original(query, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(adb, "cypher_query", fail_write)
        with pytest.raises(RuntimeError, match="migration failed"):
            await get_game_catalog()
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game_catalog'}) RETURN s.config",
    )
    assert "boss_multipliers_version" not in json.loads(rows[0][0])
    assert (await get_game_catalog()).enemies[-1].multipliers.hp == 6
