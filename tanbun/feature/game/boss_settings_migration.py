"""旧ボス共通補正を敵定義へ一度だけ移し、二段階の設定を解消する。."""

import json

from neomodel import adb

from tanbun.config.transactions import atomic

LEGACY_BOSS_DEFAULTS = {
    "boss_hp_multiplier": 3,
    "boss_mp_multiplier": 1,
    "boss_attack_multiplier": 1.25,
    "boss_defense_multiplier": 1,
    "boss_special_attack_multiplier": 1.25,
    "boss_special_defense_multiplier": 1,
    "boss_speed_multiplier": 1,
}


async def stored_settings():
    """モデル変換前に、旧共通倍率を失わずに取り出す。."""
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings) WHERE s.key IN $keys RETURN s.key, s.config",
        {"keys": ["game-balance", "game_catalog"]},
    )
    return {key: json.loads(raw) for key, raw in rows if raw}


def needs_migration(settings):
    """未設定の新規環境には何も作らない。."""
    catalog = settings.get("game_catalog")
    return (
        catalog is not None and catalog.get("boss_multipliers_version") != 1
    ) or any(key in settings.get("game-balance", {}) for key in LEGACY_BOSS_DEFAULTS)


def consolidate_boss_multipliers(catalog, balance):
    """倍率は合成するが、固定速さを持つ敵には旧共通速さを重ねない。."""
    if catalog.get("boss_multipliers_version") != 1:
        for enemy in catalog.get("enemies", []):
            if not enemy.get("boss"):
                continue
            values = enemy.setdefault("multipliers", {})
            for key, default in LEGACY_BOSS_DEFAULTS.items():
                stat = key.removeprefix("boss_").removesuffix("_multiplier")
                if stat == "speed" and enemy.get("speed") is not None:
                    continue
                common = balance.get(
                    key,
                    balance.get("boss_attack_multiplier", default)
                    if stat == "special_attack"
                    else default,
                )
                values[stat] = values.get(stat, 1) * common
        catalog["boss_multipliers_version"] = 1
    for key in LEGACY_BOSS_DEFAULTS:
        balance.pop(key, None)


async def migrate_boss_settings():
    """ロック後に再確認し、同時リクエストや再保存でも二重乗算しない。."""
    settings = await stored_settings()
    if not needs_migration(settings):
        return settings.get("game_catalog")
    async with atomic():
        await adb.cypher_query(
            "MERGE (s:AdminSettings {key:'game_catalog'}) "
            "SET s.boss_migration_lock=coalesce(s.boss_migration_lock,0)+1",
        )
        settings = await stored_settings()
        catalog = settings.get("game_catalog")
        if catalog is None:
            from .catalog import default_catalog  # noqa: PLC0415

            catalog = default_catalog().stored_document()
            catalog.pop("boss_multipliers_version", None)
            for enemy in catalog["enemies"]:
                if enemy.get("boss"):
                    enemy["multipliers"] = {}
        balance = settings.get("game-balance", {})
        consolidate_boss_multipliers(catalog, balance)
        documents = [
            {"key": "game_catalog", "config": json.dumps(catalog, ensure_ascii=False)},
        ]
        if "game-balance" in settings:
            documents.append({
                "key": "game-balance",
                "config": json.dumps(balance, ensure_ascii=False),
            })
        await adb.cypher_query(
            "UNWIND $documents AS doc MERGE (s:AdminSettings {key:doc.key}) "
            "SET s.config=doc.config",
            {"documents": documents},
        )
        await adb.cypher_query(
            "MATCH (s:AdminSettings {key:'game_catalog'}) REMOVE s.boss_migration_lock",
        )
    return catalog
