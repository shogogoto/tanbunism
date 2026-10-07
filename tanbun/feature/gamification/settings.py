"""ユーザー・リソースで共通のレベル設定."""

from neomodel import adb
from pydantic import BaseModel, Field

from .domain import DEFAULT_LEVEL_XP_COEFFICIENT


class GamificationSettings(BaseModel, frozen=True):
    """次のレベルに必要なXP = 現在Lv * 係数."""

    level_xp_coefficient: int = Field(
        default=DEFAULT_LEVEL_XP_COEFFICIENT,
        ge=1,
        le=10000,
        strict=True,
    )


async def get_gamification_settings() -> GamificationSettings:
    """保存済み設定を取得する。未設定なら既定値を使い、読み取りは書き込まない."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (settings:AdminSettings {key: 'gamification'})
        RETURN settings.level_xp_coefficient LIMIT 1
        """,
    )
    return (
        GamificationSettings(level_xp_coefficient=rows[0][0])
        if rows
        else GamificationSettings()
    )


async def update_gamification_settings(
    settings: GamificationSettings,
) -> GamificationSettings:
    """係数を保存する。獲得済みXPの台帳には触れない."""
    await adb.cypher_query(
        """
        MERGE (settings:AdminSettings {key: 'gamification'})
        SET settings.level_xp_coefficient = $level_xp_coefficient
        """,
        params=settings.model_dump(),
    )
    return settings
