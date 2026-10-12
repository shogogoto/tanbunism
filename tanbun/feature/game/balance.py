"""現在の知識量に連動するゲームバランス。敵の能力値は保存しない."""

from hashlib import blake2b
from math import ceil, log1p

from neomodel import adb
from pydantic import BaseModel, Field, model_validator


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
    # Retained solely for reading old settings/clients; no longer affect gameplay.
    relation_hp: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    relation_attack: float = Field(default=0.3, ge=0, le=100, allow_inf_nan=False)
    relation_cap: int = Field(default=30, ge=0, le=1000)
    region_hp: int = Field(default=5, ge=0, le=100)
    region_attack: int = Field(default=2, ge=0, le=100)
    enemy_variance_percent: float = Field(default=10, ge=0, le=50, allow_inf_nan=False)
    damage_attack_multiplier: float = Field(default=1, ge=0, le=10, allow_inf_nan=False)
    damage_defense_divisor: float = Field(default=2, gt=0, le=100, allow_inf_nan=False)
    damage_variance_percent: float = Field(default=20, ge=0, le=50, allow_inf_nan=False)
    max_enemies: int = Field(default=8, ge=1, le=20)
    regions_per_enemy: int = Field(default=2, ge=1, le=100)
    enemy_types: int = Field(default=3, ge=1, le=20)
    enemy_types_per_region: int = Field(default=1, ge=0, le=20)
    quizzes_per_enemy_coefficient: float = Field(
        default=1,
        gt=0,
        le=5,
        allow_inf_nan=False,
    )
    encounter_growth_rate: float = Field(default=0.2, ge=0, le=10, allow_inf_nan=False)
    min_quizzes_per_enemy: int = Field(default=1, ge=1, le=100)
    max_quizzes_per_enemy: int = Field(default=100, ge=1, le=100)
    min_enemies: int = Field(default=1, ge=1, le=20)
    max_encounter_enemies: int = Field(default=3, ge=1, le=20)
    max_encounter_enemies_per_region: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def valid_enemy_ranges(self) -> "GameBalance":
        """敵ロスターと遭遇人数の範囲が逆転していないことを検証する."""
        if self.min_quizzes_per_enemy > self.max_quizzes_per_enemy:
            msg = "敵ごとのクイズ数の下限は上限以下にしてください。"
            raise ValueError(msg)
        if self.min_enemies > self.max_encounter_enemies:
            msg = "同時出現数の下限は上限以下にしてください。"
            raise ValueError(msg)
        return self

    def enemy_type_count(self, region: int) -> int:
        """領域1を基準に、達成度帯ごとの種類数を求める."""
        return min(20, self.enemy_types + max(0, region) * self.enemy_types_per_region)

    def encounter_range(self, available: int, region: int) -> tuple[int, int]:
        """両端を成長させる。同じ種類の複数出現を許す(安全上限20)."""
        if not available:
            return 0, 0
        factor = 1 + max(0, region) * self.encounter_growth_rate
        lower = min(20, max(1, ceil(self.min_enemies * factor)))
        upper = min(20, max(lower, ceil(self.max_encounter_enemies * factor)))
        return lower, upper

    def enemy_quiz_count(self, region: int) -> int:
        """表示領域番号から固定セットの問数を算出する(切り上げ)."""
        count = ceil((max(0, region) + 1) * self.quizzes_per_enemy_coefficient)
        return min(100, max(1, count))

    def enemy_stats(
        self,
        power: int,
        relations: int,  # noqa: ARG002 - compatibility with existing callers
        region: int,
        variation_key: str = "",
    ) -> tuple[int, int]:
        """Powerのlog補正・領域補正・固定個体差。関係数は使用しない."""
        scale = log1p(max(0, power))
        hp = ceil(
            self.enemy_hp + scale * self.power_hp + region * self.region_hp,
        )
        attack = ceil(
            self.enemy_attack + scale * self.power_attack + region * self.region_attack,
        )
        if self.enemy_variance_percent and variation_key:
            digest = blake2b(variation_key.encode(), digest_size=8).digest()
            normalized = int.from_bytes(digest, "big") / (2**64 - 1)
            factor = 1 + (normalized * 2 - 1) * self.enemy_variance_percent / 100
            hp = ceil(hp * factor)
            attack = ceil(attack * factor)
        return (
            min(
                100000,
                hp,
            ),
            min(
                100000,
                attack,
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
