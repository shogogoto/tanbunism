"""設定の読み取りは更新せず、保存は指定した設定だけを変更する."""

import json
from unittest.mock import AsyncMock

from neomodel import adb

from tanbun.conftest import mark_async_test

from .balance import GameBalance, get_game_balance, update_game_balance
from .catalog import default_catalog, get_game_catalog, update_game_catalog


async def stored_documents():
    """比較用の生JSON。復元や移行を実行しない."""
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings) RETURN s.key, s.config",
    )
    return dict(rows)


@mark_async_test()
async def test_default_reads_never_create_settings(monkeypatch):
    """未保存環境への取得だけでは、既定設定や移行用ノードを作らない."""
    query = AsyncMock(wraps=adb.cypher_query)
    monkeypatch.setattr(adb, "cypher_query", query)
    assert await get_game_balance() == GameBalance()
    assert await get_game_catalog() == default_catalog()
    assert all(args.args[0].startswith("MATCH ") for args in query.await_args_list)
    assert await stored_documents() == {}


@mark_async_test()
async def test_repeated_reads_preserve_saved_boss_values_and_raw_documents(monkeypatch):
    """読み直してもボス倍率を変えず、設定JSONへ再保存しない."""
    configured = default_catalog()
    boss = next(enemy for enemy in configured.enemies if enemy.boss)
    boss.multipliers.hp = 4
    await update_game_catalog(configured)
    balance = GameBalance(base_hp=51)
    await update_game_balance(balance)
    before = await stored_documents()
    query = AsyncMock(wraps=adb.cypher_query)
    monkeypatch.setattr(adb, "cypher_query", query)
    for _ in range(2):
        loaded = await get_game_catalog()
        assert (
            next(enemy for enemy in loaded.enemies if enemy.boss).multipliers.hp
            == boss.multipliers.hp
        )
        assert await get_game_balance() == balance
    assert all(args.args[0].startswith("MATCH ") for args in query.await_args_list)
    assert await stored_documents() == before


@mark_async_test()
async def test_balance_save_does_not_create_or_rewrite_catalog():
    """バランス保存は敵定義を作らず、既存カタログにも触れない."""
    await update_game_balance(GameBalance(base_hp=51))
    assert set(await stored_documents()) == {"game-balance"}
    configured = default_catalog()
    await update_game_catalog(configured)
    before = (await stored_documents())["game_catalog"]
    await update_game_balance(GameBalance(base_hp=52))
    assert (await stored_documents())["game_catalog"] == before


@mark_async_test()
async def test_catalog_save_does_not_create_or_rewrite_balance():
    """カタログ保存は敵とバランスを暗黙に合成しない."""
    configured = default_catalog()
    await update_game_catalog(configured)
    assert set(await stored_documents()) == {"game_catalog"}
    await update_game_balance(GameBalance(base_hp=51))
    before = (await stored_documents())["game-balance"]
    await update_game_catalog(configured)
    stored = await stored_documents()
    assert stored["game-balance"] == before
    assert json.loads(stored["game_catalog"]) == configured.stored_document()
