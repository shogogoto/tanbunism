"""リソース所有者判定."""

from neomodel import adb

from tanbun.feature.domain.errors import NotFoundError
from tanbun.feature.domain.types import UUIDy, to_uuid


async def check_entry_owner(user_uid: UUIDy, entry_uid: UUIDy) -> bool:
    """エントリー所有者判定."""
    q = """
    MATCH (e:Entry {uid: $eid})
    MATCH (u:User {uid: $uid})
    OPTIONAL MATCH p = (e)-[:OWNED|PARENT]->*(u)
    RETURN e, u, p
    """
    rows, _ = await adb.cypher_query(
        q,
        params={
            "eid": to_uuid(entry_uid).hex,
            "uid": to_uuid(user_uid).hex,
        },
    )
    if len(rows) == 0:
        msg = "エントリーが見つかりません"
        raise NotFoundError(msg)
    _e, u, p = rows[0]
    if u is None:
        msg = "ユーザーが見つかりません"
        raise NotFoundError(msg)
    return p is not None
