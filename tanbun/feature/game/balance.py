"""現在の知識量に連動するゲームバランス。敵の能力値は保存しない."""

import json

from neomodel import adb

from .balance_models import Allocation, GameBalance

__all__ = ["Allocation", "GameBalance", "get_game_balance", "update_game_balance"]


async def get_game_balance() -> GameBalance:
    """未設定は仮値。旧ボス補正だけは敵定義へ一度移す。."""
    from .boss_settings_migration import (  # noqa: PLC0415
        LEGACY_BOSS_DEFAULTS,
        migrate_boss_settings,
    )

    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game-balance'}) RETURN s.config LIMIT 1",
    )
    if not rows:
        return GameBalance()
    document = json.loads(rows[0][0])
    if any(key in document for key in LEGACY_BOSS_DEFAULTS):
        await migrate_boss_settings()
    return GameBalance.model_validate(document)


async def update_game_balance(balance: GameBalance) -> GameBalance:
    """能力値を固定せず既存の敵にも即時反映する."""
    from .boss_settings_migration import migrate_boss_settings  # noqa: PLC0415

    await migrate_boss_settings()
    await adb.cypher_query(
        "MERGE (s:AdminSettings {key:'game-balance'}) SET s.config=$config",
        {"config": balance.model_dump_json()},
    )
    return balance
