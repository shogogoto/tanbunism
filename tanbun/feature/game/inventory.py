"""ゲーム内の所持品と装備。価格・効果は最初のプレイ用の仮値."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field

from .balance import Allocation, GameBalance

ProductId = Literal["bronze_sword", "steel_sword", "healing_potion", "step_potion"]


class ChestReward(BaseModel, frozen=True):
    """開封時点の中身。設定変更後も履歴を維持する."""

    gold: int = Field(default=0, ge=0)
    healing_potion: int = Field(default=0, ge=0)
    step_potion: int = Field(default=0, ge=0)


class Economy(BaseModel):
    """ゴールドは撃破、歩数回復薬は今日の復習完了から獲得する."""

    gold: int = Field(default=0, ge=0)
    inventory: dict[ProductId, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict,
    )
    weapon: Literal["bronze_sword", "steel_sword"] | None = None
    review_reward_day: str | None = None
    quiz_reward_day: str | None = None
    opened_chests: dict[str, dict[str, ChestReward]] = Field(default_factory=dict)

    @computed_field
    @property
    def weapon_attack(self) -> int:
        """カタログの装備効果をクライアントにも返す."""
        if self.weapon and self.inventory.get(self.weapon, 0) > 0:
            return CATALOG[self.weapon].amount
        return 0


class Product(BaseModel, frozen=True):
    """ショップと所持品表示で共通のカタログ."""

    id: ProductId
    name: str
    price: int | None
    kind: Literal["weapon", "hp", "moves"]
    amount: int


CATALOG = {
    p.id: p
    for p in [
        Product(id="bronze_sword", name="銅の剣", price=100, kind="weapon", amount=3),
        Product(id="steel_sword", name="鋼の剣", price=300, kind="weapon", amount=8),
        Product(id="healing_potion", name="回復薬", price=30, kind="hp", amount=20),
        Product(
            id="step_potion",
            name="歩数回復薬",
            price=None,
            kind="moves",
            amount=3,
        ),
    ]
}


def equipped_stats(
    allocation: Allocation,
    balance: GameBalance,
    economy: Economy,
) -> dict[str, int]:
    """育成値に装備の攻撃力だけを加算する."""
    stats = allocation.stats(balance)
    stats["attack"] = min(100000, stats["attack"] + economy.weapon_attack)
    return stats
