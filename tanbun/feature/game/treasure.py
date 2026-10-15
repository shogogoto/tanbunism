"""領域終端の宝箱。最新設定で未開封の箱だけを開ける."""

from fastapi import HTTPException
from pydantic import BaseModel, Field

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import UUIDy

from .balance import get_game_balance
from .inventory import ChestReward
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
        reward = ChestReward(
            gold=balance.chest_gold,
            healing_potion=balance.chest_healing_potions,
            step_potion=balance.chest_step_potions,
        )
        opened[str(body.region)] = reward
        updated.save.economy.gold += reward.gold
        for product_id in ("healing_potion", "step_potion"):
            count = getattr(reward, product_id)
            if count:
                items = updated.save.economy.inventory
                items[product_id] = items.get(product_id, 0) + count
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
