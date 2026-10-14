"""プレイヤー本人の戦闘表示設定。冒険スナップショットとは独立させる."""

from typing import Literal

from neomodel import adb
from pydantic import BaseModel

from tanbun.feature.domain.types import UUIDy, to_uuid


class BattlePreferences(BaseModel):
    """演出速度は能力値・回答の持ち時間には影響しない."""

    tempo: Literal["slow", "medium", "fast"] = "medium"


async def battle_preferences(user_id: UUIDy, update: BattlePreferences | None = None):
    """本人の速度設定をDBで共有する."""
    query = "MATCH (u:User {uid:$uid}) "
    if update is not None:
        query += "SET u.game_battle_tempo=$tempo "
    rows, _ = await adb.cypher_query(
        query + "RETURN u.game_battle_tempo",
        {"uid": to_uuid(user_id).hex, "tempo": update.tempo if update else None},
    )
    value = rows[0][0] if rows else None
    return BattlePreferences(
        tempo=value if value in {"slow", "medium", "fast"} else "medium",
    )
