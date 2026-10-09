"""戦闘クイズの時間設定。通常の復習には適用しない."""

from math import ceil
from typing import Literal

from neomodel import adb
from pydantic import BaseModel, Field

BattleQuizType = Literal["sent2term", "term2sent", "pair2rel", "rel2pair"]


class BattleSettings(BaseModel, frozen=True):
    """回答時間 = 基本秒数 * クイズ種類の重み (秒単位に切り上げ)."""

    base_seconds: int = Field(default=30, ge=5, le=300, strict=True)
    sent2term: float = Field(default=1, ge=0.5, le=5, allow_inf_nan=False)
    term2sent: float = Field(default=1.2, ge=0.5, le=5, allow_inf_nan=False)
    pair2rel: float = Field(default=1.5, ge=0.5, le=5, allow_inf_nan=False)
    rel2pair: float = Field(default=1.5, ge=0.5, le=5, allow_inf_nan=False)

    def seconds(self, kind: BattleQuizType) -> int:
        """種類ごとの上限秒数."""
        return ceil(self.base_seconds * getattr(self, kind))


async def get_battle_settings() -> BattleSettings:
    """未保存の場合は初期値。読み取りではノードを作成しない."""
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'battle'}) RETURN s.config LIMIT 1",
    )
    return BattleSettings.model_validate_json(rows[0][0]) if rows else BattleSettings()


async def update_battle_settings(settings: BattleSettings) -> BattleSettings:
    """新しく出題される問題から適用する."""
    await adb.cypher_query(
        "MERGE (s:AdminSettings {key:'battle'}) SET s.config=$config",
        {"config": settings.model_dump_json()},
    )
    return settings
