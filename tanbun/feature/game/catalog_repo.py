"""管理カタログのDB読み書きと、保存済み設定の移行境界."""

import json

from neomodel import adb

from .catalog import GameCatalog, default_catalog
from .gambit_definitions import supported_trigger
from .skill_definitions import default_normal_attack


async def get_game_catalog() -> GameCatalog:
    """未保存なら組み込み定義を返し、旧ボス補正は一度だけ統合する。."""
    from .boss_settings_migration import migrate_boss_settings  # noqa: PLC0415

    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game_catalog'}) RETURN s.config LIMIT 1",
    )
    document = json.loads(rows[0][0]) if rows else None
    if document is None or document.get("boss_multipliers_version") != 1:
        document = await migrate_boss_settings()
    if document is not None:
        triggers = document.get("triggers", [])
        rejected = {row["id"] for row in triggers if not supported_trigger(row)}
        if rejected:
            document["triggers"] = [
                row for row in triggers if row["id"] not in rejected
            ]
            for enemy in document.get("enemies", []):
                enemy["gambits"] = [
                    row
                    for row in enemy.get("gambits", [])
                    if row.get("trigger_id") not in rejected
                ]
            for group in document.get("gambit_sets", []):
                group["rules"] = [
                    row
                    for row in group.get("rules", [])
                    if row.get("trigger_id") not in rejected
                ]
            document["shop"] = [
                row
                for row in document.get("shop", [])
                if row.get("trigger_id") not in rejected
            ]
        catalog = GameCatalog.model_validate(document)
    else:
        catalog = default_catalog()
    if not any(s.id == "normal_attack" for s in catalog.skills):
        catalog.skills.append(default_normal_attack())
    return catalog


async def update_game_catalog(catalog: GameCatalog) -> GameCatalog:
    """既存の報酬・所持品が参照するIDは維持する。."""
    if not {"bronze_sword", "steel_sword", "healing_potion", "step_potion"} <= {
        i.id for i in catalog.items
    }:
        msg = "既存アイテムは削除せず、無効化してください。"
        raise ValueError(msg)
    from .boss_settings_migration import migrate_boss_settings  # noqa: PLC0415

    await migrate_boss_settings()
    await adb.cypher_query(
        "MERGE (s:AdminSettings {key:'game_catalog'}) SET s.config=$config",
        {"config": json.dumps(catalog.stored_document(), ensure_ascii=False)},
    )
    return catalog
