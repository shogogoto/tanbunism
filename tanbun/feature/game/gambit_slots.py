"""レベルから導出するガンビット枠。購入や保存スナップショットでは増えない."""

from pydantic import BaseModel

from tanbun.feature.gamification.usecase import fetch_learning_progress

from .balance import GameBalance, get_game_balance

MAX_GAMBIT_SLOTS = 10


class GambitCapacity(BaseModel):
    """利用できる枠と次の開放レベル."""

    slots: int
    next_level: int | None


def capacity_at_level(level: int, balance: GameBalance) -> GambitCapacity:
    """Lv1では1枠。指定レベルの倍数ごとに1枠開放する."""
    interval = balance.gambit_levels_per_slot
    slots = min(MAX_GAMBIT_SLOTS, 1 + max(0, level) // interval)
    return GambitCapacity(
        slots=slots,
        next_level=(max(1, level) // interval + 1) * interval
        if slots < MAX_GAMBIT_SLOTS
        else None,
    )


async def gambit_capacity(
    user_id,
    balance: GameBalance | None = None,
) -> GambitCapacity:
    """本人の学習レベルを読み、クライアント申告の値は使わない."""
    progress = await fetch_learning_progress(user_id)
    return capacity_at_level(progress.level, balance or await get_game_balance())
