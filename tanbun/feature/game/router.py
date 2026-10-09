"""認証済みユーザー本人の冒険権API."""

from fastapi import APIRouter, Response

from tanbun.feature.user.router_util import ActiveUser

from .access import AdventureAccess, consume_adventure_access, get_adventure_access
from .state import GameState, StateUpdate, read_state, write_state

router = APIRouter(prefix="/game", tags=["game"])


@router.get("/state")
async def get_game_state(user: ActiveUser, response: Response) -> GameState:
    """認証中のアカウントの冒険を読み出す."""
    response.headers["Cache-Control"] = "no-store"
    return await read_state(user.uid)


@router.put("/state")
async def put_game_state(user: ActiveUser, update: StateUpdate) -> GameState:
    """更新番号を確認して冒険を保存する."""
    return await write_state(user.uid, update)


@router.get("/adventure-access")
async def read_adventure_access(
    user: ActiveUser,
    response: Response,
) -> AdventureAccess:
    """端末の時計・ローカル保存ではなくサーバーの使用済み状態を返す."""
    response.headers["Cache-Control"] = "no-store"
    return await get_adventure_access(user.uid)


@router.post("/adventure-access/consume")
async def use_adventure_access(user: ActiveUser) -> AdventureAccess:
    """入場または休憩からの再開時に冒険権を消費する."""
    return await consume_adventure_access(user.uid)
