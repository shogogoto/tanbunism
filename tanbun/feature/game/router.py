"""認証済みユーザー本人の冒険権API."""

from fastapi import APIRouter, Response

from tanbun.feature.user.router_util import ActiveUser

from .access import AdventureAccess, consume_adventure_access, get_adventure_access

router = APIRouter(prefix="/game", tags=["game"])


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
