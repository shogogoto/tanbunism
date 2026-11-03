"""宝箱と敵の共通Gold、およびGoldから独立したアイテム抽選。."""

from math import floor, log1p, sqrt
from secrets import randbelow
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .effect_chance import FULL_CHANCE

RewardId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class GoldSettings(BaseModel):
    """Goldはアイテム抽選から独立した、敵と宝箱の共通計算."""

    model_config = ConfigDict(extra="forbid")
    base: int = Field(default=10, ge=0, le=100000)
    power_coefficient: float = Field(default=2, ge=0, le=100000, allow_inf_nan=False)
    power_formula: Literal["sqrt", "log"] = "sqrt"
    power_reference: float = Field(default=100, ge=0.01, le=10**9, allow_inf_nan=False)
    achievement_growth_percent: float = Field(
        default=20,
        ge=0,
        le=1000,
        allow_inf_nan=False,
    )
    variance_percent: float = Field(default=10, ge=0, le=50, allow_inf_nan=False)
    enemy_multiplier: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    chest_multiplier: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)


def roll_gold(
    settings: GoldSettings,
    power: int,
    achievement: int,
    source: Literal["enemy", "chest"],
    *,
    variation: float | None = None,
) -> int:
    """サーバーのPower・達成度と開封/撃破ごとのバラツキを適用する."""
    scaled = max(0, power) / settings.power_reference
    bonus = sqrt(scaled) if settings.power_formula == "sqrt" else log1p(scaled)
    growth = 1 + max(0, achievement - 1) * settings.achievement_growth_percent / 100
    roll = (
        randbelow(20001) / 10000 - 1
        if variation is None
        else max(-1, min(1, variation))
    )
    variance = 1 + roll * settings.variance_percent / 100
    return min(
        1000000,
        floor(
            (settings.base + bonus * settings.power_coefficient)
            * growth
            * variance
            * getattr(settings, f"{source}_multiplier"),
        ),
    )


class RewardEntry(BaseModel):
    """同じ段階の候補から重みで一つ選ぶ。段階はアイテム本体と独立する。."""

    model_config = ConfigDict(extra="forbid")
    item_id: RewardId
    tier: int = Field(default=1, ge=1, le=100)
    weight: int = Field(default=1, ge=1, le=10000)
    quantity: int = Field(default=1, ge=1, le=100)


class RewardTable(BaseModel):
    """段階別のアイテム抽選。Goldはここでは設定しない。."""

    model_config = ConfigDict(extra="forbid")
    id: RewardId
    name: str = Field(min_length=1, max_length=80)
    tier_interval: int = Field(default=5, ge=1, le=1000)
    draws: int = Field(default=1, ge=0, le=10)
    item_chance: int = Field(default=100, ge=0, le=100)
    entries: list[RewardEntry] = Field(default_factory=list, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def separate_old_gold(cls, values):
        """旧保存データを読めるようにし、表単位の旧Gold設定は再出力しない."""
        if isinstance(values, dict):
            return {
                key: value
                for key, value in values.items()
                if key not in {"base_gold", "gold_per_power", "power_reference"}
            }
        return values

    @model_validator(mode="after")
    def unique_entries(self):
        """同一段階の同一アイテムは一行で重み・個数を設定する。."""
        identities = [(entry.tier, entry.item_id) for entry in self.entries]
        if len(set(identities)) != len(identities):
            msg = "同じ段階のアイテムが重複しています。"
            raise ValueError(msg)
        return self


class RewardSettings(BaseModel):
    """用途ごとの参照だけを分離し、表の計算は共用する。."""

    model_config = ConfigDict(extra="forbid")
    gold: GoldSettings = Field(default_factory=GoldSettings)
    tables: list[RewardTable] = Field(default_factory=list, max_length=100)
    chest_table_id: RewardId | None = None

    @model_validator(mode="after")
    def valid_tables(self):
        """削除済みの報酬表や重複IDを保存しない。."""
        identities = [table.id for table in self.tables]
        if len(set(identities)) != len(identities):
            msg = "報酬表のIDが重複しています。"
            raise ValueError(msg)
        if self.chest_table_id and self.chest_table_id not in identities:
            msg = "宝箱で使う報酬表が見つかりません。"
            raise ValueError(msg)
        return self


class RewardItem(BaseModel, frozen=True):
    """取得時点の名前・個数。後の改名・削除で履歴を書き換えない。."""

    name: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=1000)


class RolledReward(BaseModel):
    """抽選結果だけを保持し、所持品への反映は呼び出し側のCASで行う。."""

    items: dict[RewardId, RewardItem] = Field(default_factory=dict)


def eligible_entries(table: RewardTable, achievement: int, items: dict[str, str]):
    """未定義の上位段階では、利用可能な最も近い下位段階へ戻す。."""
    tier = 1 + max(0, achievement) // table.tier_interval
    entries = [e for e in table.entries if e.item_id in items and e.tier <= tier]
    selected = max((e.tier for e in entries), default=0)
    return [e for e in entries if e.tier == selected]


def roll_reward(
    table: RewardTable,
    achievement: int,
    items: dict[str, str],
) -> RolledReward:
    """サーバーの達成度から候補を選び、入手率と重みでアイテムを抽選する。."""
    result = RolledReward()
    entries = eligible_entries(table, achievement, items)
    if not entries or not table.item_chance:
        return result
    total = sum(entry.weight for entry in entries)
    for _ in range(table.draws):
        if (
            table.item_chance < FULL_CHANCE
            and randbelow(FULL_CHANCE) >= table.item_chance
        ):
            continue
        draw = randbelow(total)
        for entry in entries:
            draw -= entry.weight
            if draw >= 0:
                continue
            previous = result.items.get(entry.item_id)
            result.items[entry.item_id] = RewardItem(
                name=items[entry.item_id],
                quantity=(previous.quantity if previous else 0) + entry.quantity,
            )
            break
    return result
