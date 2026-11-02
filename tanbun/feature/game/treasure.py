"""領域終端の宝箱。最新設定で未開封の箱だけを開ける."""

from fastapi import HTTPException
from pydantic import BaseModel, Field

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import UUIDy
from tanbun.feature.gamification.resource import fetch_resource_growth

from .balance import GameBalance, get_game_balance
from .catalog import get_game_catalog
from .inventory import ChestReward, Economy
from .rewards import roll_reward
from .state import (
    PLACES_PER_REGION,
    GameState,
    StateUpdate,
    compare_and_save,
    read_state,
)


class ChestRequest(BaseModel):
    """表示中の領域と更新番号。報酬自体は受け取らない."""

    revision: int = Field(ge=0)
    region: int = Field(ge=0, le=1000)


async def chest_reward(
    user_id: UUIDy,
    resource_id: str,
    balance: GameBalance,
) -> ChestReward:
    """報酬表の抽選と、宝箱共通の固定アイテムをまとめる。."""
    catalog = await get_game_catalog()
    table = (
        next(
            (
                table
                for table in catalog.rewards.tables
                if table.id == catalog.rewards.chest_table_id
            ),
            None,
        )
        if catalog.rewards
        else None
    )
    rolled = None
    if table:
        growth = await fetch_resource_growth(user_id, only_resource=resource_id)
        skills = {skill.id: skill for skill in catalog.skills}
        available = {
            item.id: item.name
            for item in catalog.items
            if item.enabled and skills[item.skill_id].enabled
        }
        rolled = roll_reward(
            table,
            growth[0].power if growth else 0,
            growth[0].level if growth else 0,
            available,
        )
    return ChestReward(
        gold=rolled.gold if rolled else balance.chest_gold,
        healing_potion=balance.chest_healing_potions,
        step_potion=balance.chest_step_potions,
        items=rolled.items if rolled else {},
    )


def credit_chest_reward(economy: Economy, reward: ChestReward) -> None:
    """所持数だけ増やし、使用中の装備や耐久を上書きしない。."""
    economy.gold += reward.gold
    for product_id in ("healing_potion", "step_potion"):
        count = getattr(reward, product_id)
        if count:
            economy.inventory[product_id] = economy.inventory.get(product_id, 0) + count
    for item_id, item in reward.items.items():
        economy.inventory[item_id] = economy.inventory.get(item_id, 0) + item.quantity


async def open_chest(user_id: UUIDy, body: ChestRequest) -> GameState:
    """端まで到達した領域で無料開封。競合時も二重に報酬を付けない."""
    previous = await read_state(user_id)
    if previous.revision != body.revision:
        raise HTTPException(409, "冒険が更新されました。もう一度操作してください。")
    run = previous.save.run
    if (
        not run
        or not run.hp
        or run.phase not in {"path", "rest", "cleared"}
        or previous.save.battle
        or previous.save.battleFeedback
    ):
        raise HTTPException(409, "今は宝箱を開けられません。")
    dungeon_map = previous.save.maps.get(run.resourceId)
    places = (
        [p for p in dungeon_map.places if p.region == body.region]
        if dungeon_map
        else []
    )
    chest_id = f"@chest:{body.region}"
    if len(places) < PLACES_PER_REGION or dungeon_map.current not in {
        places[-1].id,
        chest_id,
    }:
        raise HTTPException(422, "領域の最後の地点から宝箱へ移動してください。")
    updated = previous.model_copy(deep=True)
    updated.save.maps[run.resourceId].current = chest_id
    opened = updated.save.economy.opened_chests.setdefault(run.resourceId, {})
    if str(body.region) not in opened:
        balance = await get_game_balance()
        reward = await chest_reward(user_id, run.resourceId, balance)
        opened[str(body.region)] = reward
        credit_chest_reward(updated.save.economy, reward)
    updated.revision += 1
    async with atomic():
        rows = await compare_and_save(
            user_id,
            StateUpdate(revision=previous.revision, save=updated.save),
            updated,
        )
        if not rows:
            raise HTTPException(409, "冒険が更新されました。もう一度操作してください。")
    return updated
