"""現在の知識量に連動するゲームバランス。敵の能力値は保存しない."""

from math import ceil, log1p

from neomodel import adb
from pydantic import BaseModel, Field


class GameBalance(BaseModel, frozen=True):
    """数値は仮値。既存の敵にも読み取り時に適用する."""

    base_hp: int = Field(default=35, ge=1, le=10000)
    base_attack: int = Field(default=10, ge=1, le=1000)
    base_defense: int = Field(default=1, ge=0, le=1000)
    base_seconds: int = Field(default=45, ge=5, le=300)
    hp_per_point: int = Field(default=5, ge=1, le=100)
    attack_per_point: int = Field(default=2, ge=1, le=100)
    defense_per_point: int = Field(default=1, ge=1, le=100)
    seconds_per_point: int = Field(default=3, ge=1, le=30)
    enemy_hp: int = Field(default=20, ge=1, le=10000)
    enemy_attack: int = Field(default=12, ge=1, le=1000)
    power_hp: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    power_attack: float = Field(default=0.5, ge=0, le=100, allow_inf_nan=False)
    relation_hp: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    relation_attack: float = Field(default=0.3, ge=0, le=100, allow_inf_nan=False)
    relation_cap: int = Field(default=30, ge=0, le=1000)
    region_hp: int = Field(default=5, ge=0, le=100)
    region_attack: int = Field(default=2, ge=0, le=100)
    max_enemies: int = Field(default=8, ge=1, le=20)
    regions_per_enemy: int = Field(default=2, ge=1, le=100)

    def enemy_stats(self, power: int, relations: int, region: int) -> tuple[int, int]:
        """Powerはlog補正、単文の関係数は上限付き。検索の重みには依存しない."""
        scale = log1p(max(0, power))
        delta = min(self.relation_cap, max(0, relations))
        return (
            min(
                100000,
                ceil(
                    self.enemy_hp
                    + scale * self.power_hp
                    + delta * self.relation_hp
                    + region * self.region_hp,
                ),
            ),
            min(
                100000,
                ceil(
                    self.enemy_attack
                    + scale * self.power_attack
                    + delta * self.relation_attack
                    + region * self.region_attack,
                ),
            ),
        )


class Allocation(BaseModel):
    """Lvはポイント数だけに影響する。振り直しに費用はかからない."""

    hp: int = Field(default=0, ge=0, le=10000)
    attack: int = Field(default=0, ge=0, le=10000)
    defense: int = Field(default=0, ge=0, le=10000)
    seconds: int = Field(default=0, ge=0, le=10000)

    def stats(self, balance: GameBalance) -> dict[str, int]:
        """最大HP・攻・守・持ち時間を導出する."""
        return {
            "maxHp": min(100000, balance.base_hp + self.hp * balance.hp_per_point),
            "attack": min(
                100000,
                balance.base_attack + self.attack * balance.attack_per_point,
            ),
            "defense": min(
                100000,
                balance.base_defense + self.defense * balance.defense_per_point,
            ),
            "answerSeconds": min(
                1500,
                balance.base_seconds + self.seconds * balance.seconds_per_point,
            ),
        }


async def get_game_balance() -> GameBalance:
    """未設定は仮値。読み取りでは書き込まない."""
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game-balance'}) RETURN s.config LIMIT 1",
    )
    return GameBalance.model_validate_json(rows[0][0]) if rows else GameBalance()


async def update_game_balance(balance: GameBalance) -> GameBalance:
    """能力値を固定せず既存の敵にも即時反映する."""
    await adb.cypher_query(
        "MERGE (s:AdminSettings {key:'game-balance'}) SET s.config=$config",
        {"config": balance.model_dump_json()},
    )
    return balance
