"""repo."""

from typing import Any, Literal
from uuid import UUID

from more_itertools import collapse
from neomodel import adb

from tanbun.feature.domain.errors import DomainError as DomainError
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.entry.namespace import resource_infos_by_resource_uids
from tanbun.feature.recommendation.pagerank.cypher import cached_rank
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.tanbun.domain import (
    Tanbun as Tanbun,
)
from tanbun.feature.tanbun.domain import (
    TanbunAdjacency,
    TanbunSearchResult,
)
from tanbun.feature.tanbun.repo.adj import AdjType
from tanbun.feature.tanbun.repo.clause import OrderBy, WherePhrase
from tanbun.feature.tanbun.repo.detail import fetch_tanbuns_with_detail

from .cypher import q_adjacency_uids, q_search
from .cypher import q_stats as q_stats
from .cypher import q_where_tanbun as q_where_tanbun


async def search_total(
    s: str,
    where: WherePhrase = WherePhrase.CONTAINS,
    filter_resource_uids: list[UUIDy] | None = None,
    only_with_term: bool = False,  # noqa: FBT001, FBT002
) -> int:
    """検索文字列にマッチする単文総数."""
    q = q_search(where, filter_resource_uids, only_with_term)
    q += """
        RETURN COUNT(sent)
    """
    res, _ = await adb.cypher_query(
        q,
        params={
            "s": s,
            "resource_uids": [to_uuid(uid).hex for uid in filter_resource_uids]
            if filter_resource_uids
            else [],
        },
    )
    return res[0][0]


async def search_tanbun_ids(  # noqa: PLR0917
    s: str,
    where: WherePhrase = WherePhrase.CONTAINS,
    paging: Paging = Paging(),
    order_by: OrderBy | None = OrderBy(),
    belong_resource_uids: list[UUIDy] | None = None,
    only_with_term: bool = False,  # noqa: FBT001, FBT002
    exclude_sent_ids: list[UUIDy] | None = None,
    do_print: bool = False,  # noqa: FBT001, FBT002
    sort: Literal["score", "pagerank"] = "score",
) -> list[UUID]:
    """用語、文のいずれかでマッチする単文のUUIDを返す."""
    q = q_search(
        where,
        belong_resource_uids,
        only_with_term,
        exclude_sent_ids=exclude_sent_ids,
    )
    rank_clause = ""
    ordering = order_by.phrase() if order_by else ""
    if sort == "pagerank":
        rank_clause = f"""
            MATCH (rank_resource:Resource {{uid: sent.resource_uid}})
            WITH *, {cached_rank("sent", "rank_resource")} AS rank_score
        """
        direction = "DESC" if order_by is None or order_by.desc else "ASC"
        ordering = (
            f"ORDER BY rank_score IS NULL ASC, rank_score {direction}, "
            "stats.score DESC, sent.uid ASC"
        )
    q += f"""
        {q_stats("sent", order_by)}
        {rank_clause}
        {ordering}
        {paging.phrase()}
        RETURN
            sent.uid AS sent_uid
    """
    if do_print:
        print(q)  # noqa: T201
    rows, _ = await adb.cypher_query(
        q,
        params={
            "s": s,
            "resource_uids": [to_uuid(uid).hex for uid in belong_resource_uids]
            if belong_resource_uids
            else [],
            "exclude_uids": [to_uuid(uid).hex for uid in exclude_sent_ids]
            if exclude_sent_ids
            else [],
        },
    )
    return res2uidstrs(rows)


async def search_tanbun(  # noqa: PLR0917
    s: str,
    where: WherePhrase = WherePhrase.CONTAINS,
    paging: Paging = Paging(),
    order_by: OrderBy | None = OrderBy(),
    filter_resource_uids: list[UUIDy] | None = None,
    only_with_term: bool = False,  # noqa: FBT001, FBT002
    exclude_sent_ids: list[UUIDy] | None = None,
    do_print: bool = False,  # noqa: FBT001, FBT002
    sort: Literal["score", "pagerank"] = "score",
) -> TanbunSearchResult:
    """用語、文のいずれかでマッチする単文の検索結果を返す."""
    kn_uids = await search_tanbun_ids(
        s,
        where,
        paging,
        order_by,
        filter_resource_uids,
        only_with_term,
        exclude_sent_ids,
        do_print,
        sort,
    )
    d = await fetch_tanbuns_with_detail(kn_uids, order_by=order_by)
    ls = [d[to_uuid(uid).hex] for uid in kn_uids]
    ranks = {}
    if sort == "pagerank" and kn_uids:
        rows, _ = await adb.cypher_query(
            f"""
            UNWIND $uids AS uid
            MATCH (sent:Sentence {{uid: uid}})
            MATCH (resource:Resource {{uid: sent.resource_uid}})
            RETURN sent.uid, {cached_rank("sent", "resource")}
            """,
            params={"uids": [to_uuid(uid).hex for uid in kn_uids]},
        )
        ranks = dict(rows)
    return TanbunSearchResult(
        total=await search_total(s, where, filter_resource_uids, only_with_term),
        data=ls,
        pagerank_scores=ranks,
        resource_infos=await resource_infos_by_resource_uids({
            k.resource_uid for k in ls
        }),
    )


def res2uidstrs(res: tuple) -> list[UUID]:
    """neo4j レスポンスからuuidのセットを返す."""

    def is_valid_uuid(uuid_string) -> bool:
        try:
            UUID(uuid_string)
            return True  # noqa: TRY300
        except ValueError:
            return False
        except TypeError:
            return False

    return list(filter(is_valid_uuid, collapse(res, base_type=UUID)))


async def adj_tanbun_ids(
    sent_uids: list[UUIDy],
    radius: int = 1,
    only_with_term: bool = False,  # noqa: FBT001, FBT002
    types: list[AdjType] | None = None,
    do_print: bool = False,  # noqa: FBT001, FBT002
) -> tuple[list[UUID], Any]:
    """隣接単文のidを返す."""
    q_term = "<-[:DEF]-(:Term)" if only_with_term else ""
    q = rf"""
        UNWIND $uids AS uid
        MATCH (sent: Sentence {{uid: uid}})
            {q_term}
        {q_adjacency_uids("sent", "sent", radius)}
        RETURN
            sent.uid AS sent_uid
            , premises
            , conclusions
            , refers
            , referreds
            , details
            , abstracts
            , examples
        """
    if do_print:
        print(q)  # noqa: T201
    rows, _ = await adb.cypher_query(
        q,
        params={"uids": [to_uuid(uid).hex for uid in sent_uids]},
    )
    return res2uidstrs(rows), rows


async def adj_tanbun(
    sent_uids: list[UUIDy],
    radius: int = 1,
    only_with_term: bool = False,  # noqa: FBT001, FBT002
    do_print: bool = False,  # noqa: FBT001, FBT002
) -> list[TanbunAdjacency]:
    """隣接単文を返す."""
    uids, rows = await adj_tanbun_ids(sent_uids, radius, only_with_term, do_print)
    knowdes = await fetch_tanbuns_with_detail(uids)
    ls = []
    for row in rows:
        sent, premises, conclusions, refers, referreds, details, abstracts, examples = (
            row
        )
        adj = TanbunAdjacency(
            center=knowdes[sent],
            details=[knowdes[d] for d in details],
            premises=[knowdes[p] for p in premises],
            conclusions=[knowdes[c] for c in conclusions],
            refers=[knowdes[r] for r in refers],
            referreds=[knowdes[r] for r in referreds],
            abstracts=[knowdes[a] for a in abstracts],
            examples=[knowdes[e] for e in examples],
        )
        ls.append(adj)
    return ls
