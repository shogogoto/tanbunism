"""ゲーム内の所持品と装備。価格・効果は最初のプレイ用の仮値."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from .balance import Allocation, GameBalance

ProductId = str


class EquipmentBuff(BaseModel):
    """アイテムの状態バフを装備時に固定した、装備中だけの能力補正."""

    stat: Literal[
        "attack",
        "defense",
        "specialAttack",
        "specialDefense",
        "maxHp",
        "maxMp",
        "speed",
        "damageDealt",
        "damageTaken",
    ]
    affected_element: str | None = None
    amount: int = Field(ge=0, le=10000)
    amount_mode: Literal["fixed", "percent"] = "fixed"
    duration_turns: Literal["infinite"] = "infinite"


class ChestReward(BaseModel, frozen=True):
    """開封時点の中身。設定変更後も履歴を維持する."""

    gold: int = Field(default=0, ge=0)
    healing_potion: int = Field(default=0, ge=0)
    step_potion: int = Field(default=0, ge=0)


class DungeonKey(BaseModel, frozen=True):
    """配布履歴を兼ねる、リソースを指定したダンジョン開放アイテム."""

    resource_id: str = Field(max_length=64)
    name: str = Field(max_length=1000)


class Economy(BaseModel):
    """ゴールドは撃破、歩数回復薬は今日の復習完了から獲得する."""

    gold: int = Field(default=0, ge=0)
    inventory: dict[ProductId, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict,
    )
    weapon: str | None = None
    equipment_attack: int | None = Field(default=None, ge=0, le=10000)
    equipment_buffs: list[EquipmentBuff] | None = Field(default=None, max_length=20)
    weapon_max_uses: int = Field(default=30, ge=1, le=10000)
    remaining_uses: dict[str, Annotated[int, Field(ge=1, le=10000)]] = Field(
        default_factory=dict,
    )
    review_reward_day: str | None = None
    quiz_reward_day: str | None = None
    opened_chests: dict[str, dict[str, ChestReward]] = Field(default_factory=dict)
    dungeon_keys: dict[str, DungeonKey] = Field(default_factory=dict)
    unlocked_triggers: list[str] = Field(default_factory=list, max_length=1000)

    @computed_field
    @property
    def weapon_attack(self) -> int:
        """カタログの装備効果をクライアントにも返す."""
        if self.weapon and self.inventory.get(self.weapon, 0) > 0:
            if self.equipment_attack is not None:
                return self.equipment_attack
            legacy = CATALOG.get(self.weapon)
            return legacy.amount if legacy else 0
        return 0


class Product(BaseModel, frozen=True):
    """ショップと所持品表示で共通のカタログ."""

    id: ProductId
    name: str
    price: int | None
    kind: Literal["weapon", "hp", "mp", "moves", "skill", "dungeon_unlock", "gambit"]
    trigger_id: str | None = None
    resource_id: str | None = None
    amount: int
    amount_mode: Literal["fixed", "percent"] = "fixed"
    icon: str = "flask"
    skill_id: str | None = None
    skill_name: str | None = None
    enabled: bool = True
    max_uses: int = Field(default=1, ge=1, le=10000)
    equipment_buffs: list[EquipmentBuff] = Field(default_factory=list, max_length=20)
    element_names: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def gambit_reference(self):
        """ガンビット商品の購入先を空のまま登録しない。."""
        if self.kind == "gambit" and not self.trigger_id:
            msg = "ガンビット商品にはトリガーを指定してください。"
            raise ValueError(msg)
        return self


CATALOG = {
    p.id: p
    for p in [
        Product(
            id="bronze_sword",
            name="銅の剣",
            price=100,
            kind="weapon",
            amount=3,
            max_uses=30,
        ),
        Product(
            id="steel_sword",
            name="鋼の剣",
            price=300,
            kind="weapon",
            amount=8,
            max_uses=30,
        ),
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


def consume_use(economy: Economy, item_id: str, max_uses: int) -> bool:
    """使用中の一個分だけ減らす。持ち替えや再購入で耐久を回復させない."""
    count = economy.inventory.get(item_id, 0)
    if count < 1:
        return False
    remaining = min(economy.remaining_uses.get(item_id, max_uses), max_uses) - 1
    if remaining:
        economy.remaining_uses[item_id] = remaining
        return False
    economy.inventory[item_id] = count - 1
    economy.remaining_uses.pop(item_id, None)
    if economy.weapon == item_id:
        economy.weapon = None
        economy.equipment_attack = 0
        economy.equipment_buffs = None
    return True


def wear_weapon(economy: Economy, hits: int) -> bool:
    """正答による攻撃一回につき一回消費し、破損後の攻撃では消費しない."""
    for _ in range(hits):
        if not economy.weapon:
            break
        if consume_use(economy, economy.weapon, economy.weapon_max_uses):
            return True
    return False


def equipment_wears_on_damage(economy: Economy) -> bool:
    """攻撃補正のない装備は防具として、命中ではなく被ダメージで消耗する."""
    return bool(economy.equipment_buffs) and not any(
        buff.stat in {"attack", "specialAttack", "damageDealt"}
        for buff in economy.equipment_buffs
    )


def equipped_stats(
    allocation: Allocation,
    balance: GameBalance,
    economy: Economy,
) -> dict[str, int]:
    """元の育成値を基準として、装備に付いたバフだけを加算する."""
    stats = allocation.stats(balance)
    if (
        economy.weapon
        and economy.inventory.get(economy.weapon, 0) > 0
        and economy.equipment_buffs is not None
    ):
        base = dict(stats)
        for buff in economy.equipment_buffs:
            if buff.stat not in stats:
                continue
            delta = (
                base[buff.stat] * buff.amount // 100
                if buff.amount_mode == "percent"
                else buff.amount
            )
            stats[buff.stat] = min(100000, stats[buff.stat] + delta)
    else:
        stats["attack"] = min(100000, stats["attack"] + economy.weapon_attack)
    return stats
