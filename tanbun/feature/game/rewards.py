"""宝箱と将来の敵ドロップが共有する報酬表。抽選にプレイヤー状態を渡さない。."""

from math import floor, sqrt
from secrets import randbelow
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .effect_chance import FULL_CHANCE

RewardId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class RewardEntry(BaseModel):
    """同じ段階の候補から重みで一つ選ぶ。段階はアイテム本体と独立する。."""

    model_config = ConfigDict(extra="forbid")
    item_id: RewardId
    tier: int = Field(default=1, ge=1, le=100)
    weight: int = Field(default=1, ge=1, le=10000)
    quantity: int = Field(default=1, ge=1, le=100)


class RewardTable(BaseModel):
    """Goldと段階別の抽選。初級・中級などの実体は管理者が選ぶ。."""

    model_config = ConfigDict(extra="forbid")
    id: RewardId
    name: str = Field(min_length=1, max_length=80)
    base_gold: int = Field(default=30, ge=0, le=100000)
    gold_per_power: float = Field(default=10, ge=0, le=100000, allow_inf_nan=False)
    power_reference: float = Field(default=100, ge=1, le=10**9, allow_inf_nan=False)
    tier_interval: int = Field(default=5, ge=1, le=1000)
    draws: int = Field(default=1, ge=0, le=10)
    item_chance: int = Field(default=100, ge=0, le=100)
    entries: list[RewardEntry] = Field(default_factory=list, max_length=200)

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

    gold: int = Field(ge=0, le=1000000)
    items: dict[RewardId, RewardItem] = Field(default_factory=dict)


def eligible_entries(table: RewardTable, achievement: int, items: dict[str, str]):
    """未定義の上位段階では、利用可能な最も近い下位段階へ戻す。."""
    tier = 1 + max(0, achievement) // table.tier_interval
    entries = [e for e in table.entries if e.item_id in items and e.tier <= tier]
    selected = max((e.tier for e in entries), default=0)
    return [e for e in entries if e.tier == selected]


def roll_reward(
    table: RewardTable,
    power: int,
    achievement: int,
    items: dict[str, str],
) -> RolledReward:
    """サーバー側の実Power・達成度で計算。抽選ごとの付与率と重みを分離する。."""
    gold = min(
        1000000,
        floor(
            table.base_gold
            + table.gold_per_power * sqrt(max(0, power) / table.power_reference),
        ),
    )
    result = RolledReward(gold=gold)
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
