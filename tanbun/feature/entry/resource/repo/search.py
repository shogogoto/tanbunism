"""リソース検索."""

from typing import get_args

from neomodel import adb

from tanbun.feature.entry.domain import (
    ResourceInfo,
    ResourceOrderKey,
    ResourceSearchResult,
    StatsOrderKey,
    UserOrderKey,
)
from tanbun.feature.entry.mapper import MResource
from tanbun.feature.entry.resource.stats.domain import ResourceStats
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.user.public_schema import UserReadPublic


async def search_resources(  # noqa: PLR0917
    search_str: str,
    paging: Paging = Paging(),
    search_user: str = "",  # username or display name
    keys: list[ResourceOrderKey | StatsOrderKey | UserOrderKey] | None = None,
    desc: bool = True,  # noqa: FBT001, FBT002
    do_print: bool = False,  # noqa: FBT001, FBT002
) -> ResourceSearchResult:
    """リソース検索."""
    if keys is None:
        keys = ["username"]
    skeys = []
    for k in keys:
        if k in get_args(ResourceOrderKey):
            q_ord = f"r.{k}"
        elif k in get_args(StatsOrderKey):
            q_ord = f"stat.{k}"
        elif k in get_args(UserOrderKey):
            q_ord = f"u.{k}"
        else:
            msg = f"Unknown order key: {k}"
            raise TypeError(msg)
        skeys.append(q_ord)

    q = f"""
        MATCH (r:Resource WHERE r.title CONTAINS $s)
            -[:STATS]->(stat:ResourceStatsCache)
            , (u:User)<-[:OWNED|PARENT]-*(r)
        WHERE
            u.username CONTAINS $u
            OR u.display_name CONTAINS $u
            OR u.uid CONTAINS $u

        WITH r, stat, u
        ORDER BY {", ".join(skeys)} {((desc and "DESC") or "ASC")}

        WITH COLLECT({{r:r, stat:stat, u:u}}) AS results
        {paging.return_stmt("results")}
    """

    if do_print:
        print(q)  # noqa: T201

    params = {
        "s": search_str,
        "u": search_user,
        **paging.params,
    }

    rows, _ = await adb.cypher_query(q, params=params)
    if not rows:
        return ResourceSearchResult(total=0, data=[])

    total, page = rows[0]
    infos = []
    for row in page:
        # r, stat, u = row  # from old query
        r, stat, u = row["r"], row["stat"], row["u"]
        info = ResourceInfo(
            user=UserReadPublic.model_validate(u),
            resource=MResource.freeze_dict(r),
            resource_stats=ResourceStats.model_validate(stat),
        )
        infos.append(info)

    return ResourceSearchResult(
        total=total,
        data=infos,
    )
