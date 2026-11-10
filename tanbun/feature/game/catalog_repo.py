"""管理カタログの読み書き。取得時に保存データを変換・更新しない."""

import json

from neomodel import adb

from .catalog import GameCatalog, default_catalog
from .skill_definitions import default_normal_attack


async def get_game_catalog() -> GameCatalog:
    """保存した定義を読み、未保存なら既定値を返す。DBには書き込まない."""
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game_catalog'}) RETURN s.config LIMIT 1",
    )
    catalog = (
        GameCatalog.model_validate(json.loads(rows[0][0]))
        if rows
        else default_catalog()
    )
    if not any(skill.id == "normal_attack" for skill in catalog.skills):
        catalog.skills.append(default_normal_attack())
    return catalog


async def update_game_catalog(catalog: GameCatalog) -> GameCatalog:
    """既存の所持品が参照するIDを維持し、指定した定義だけを保存する."""
    if not {"bronze_sword", "steel_sword", "healing_potion", "step_potion"} <= {
        item.id for item in catalog.items
    }:
        msg = "既存アイテムは削除せず、無効化してください。"
        raise ValueError(msg)
    await adb.cypher_query(
        "MERGE (s:AdminSettings {key:'game_catalog'}) SET s.config=$config",
        {"config": json.dumps(catalog.stored_document(), ensure_ascii=False)},
    )
    return catalog
