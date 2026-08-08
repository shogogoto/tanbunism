"""クイズの選択肢候補."""

from typing import Annotated
from uuid import UUID

from neomodel import adb
from pydantic import Field, TypeAdapter

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.parsing.sysnet.sysnode import DUMMY_SENTENCE
from tanbun.feature.quiz.eligibility import filter_defined_sentence_ids
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.tanbun.repo import search_tanbun_ids
from tanbun.feature.tanbun.repo.clause import OrderBy


async def fetch_sent2resource_id(sent_ids: list[UUIDy]):
    """単文IDをそのリソースのIDへ変換."""
    q = """
        UNWIND $sent_uids AS sent_uid
        MATCH (sent:Sentence {uid: sent_uid})
        RETURN DISTINCT sent.resource_uid
    """
    rows, _ = await adb.cypher_query(
        q,
        params={"sent_uids": [to_uuid(uid).hex for uid in sent_ids]},
    )
    return [row[0] for row in rows]


ENOUGH_PAGING = Paging(size=999999)  # 十分な大きさ


async def list_candidates_in_resource(
    target_sent_ids: list[UUIDy],
    only_with_term: bool = False,  # noqa: FBT001, FBT002
    exclude_sent_ids: list[UUIDy] | None = None,
) -> list[UUID]:
    """リソース内全ての単文uidを選択肢候補として列挙."""
    if exclude_sent_ids is None:
        exclude_sent_ids = []

    rs_uids = await fetch_sent2resource_id(target_sent_ids)
    candidates = await search_tanbun_ids(
        "",
        paging=ENOUGH_PAGING,
        order_by=None,  # 無駄な並び替え省く
        belong_resource_uids=rs_uids,
        only_with_term=only_with_term,
        exclude_sent_ids=target_sent_ids + exclude_sent_ids,
    )
    return await filter_defined_sentence_ids(candidates)


type Radius = Annotated[int, Field(gt=0, title="探索半径")]
r_adapter = TypeAdapter(Radius)


async def list_candidates_by_radius(
    target_sent_ids: list[UUIDy],
    radius: int,
    only_with_term: bool = False,  # noqa: FBT001, FBT002
    exclude_sent_ids: list[UUIDy] | None = None,
) -> list[UUID]:
    """距離指定で選択肢候補を列挙."""
    if exclude_sent_ids is None:
        exclude_sent_ids = []
    r = r_adapter.validate_python(radius)
    q_term = "<-[:DEF]-(:Term)" if only_with_term else ""
    # search_tanbun あたりをcallするだけにしたかったが
    # locationを持たせる設計になっているため合わない
    q = f"""
        UNWIND $sent_uids AS sent_uid
        MATCH (sent:Sentence {{uid: sent_uid}})
        // dist=1.. にすることで sent_uidを含めない
        OPTIONAL MATCH p = (sent)-[]-{{1, {r}}}(e:Sentence)
            {q_term}
        WHERE e.uid IS NOT NULL
          AND e.val <> $dummy_sentence
          AND NOT e.uid IN $exclude_uids
        RETURN DISTINCT e.uid
    """
    uids = [to_uuid(uid).hex for uid in target_sent_ids]
    rows, _ = await adb.cypher_query(
        q,
        params={
            "sent_uids": uids,
            "exclude_uids": [to_uuid(uid).hex for uid in exclude_sent_ids],
            "dummy_sentence": DUMMY_SENTENCE,
        },
    )
    return [row[0] for row in rows]


async def filter_has_quiz(
    sent_ids: list[UUIDy],
    limit: int,
) -> list[UUID]:
    """クイズが既にある単文を除外."""
    q = """
        UNWIND $sent_uids AS sent_uid
        MATCH (sent:Sentence {{uid: sent_uid}})
        OPTIONAL MATCH (sent)<-[:QUIZ_TARGET]-(q:Quiz)
        WITH sent, COUNT(q) AS quiz_count
        WHERE quiz_count < $limit
        RETURN sent.uid
    """
    uids = [to_uuid(uid).hex for uid in sent_ids]
    rows, _ = await adb.cypher_query(
        q,
        params={"sent_uids": uids, "limit": limit},
    )
    return [row[0] for row in rows]


# 重要な単文を選択肢に混ぜて単純接触効果による学習効果を狙う
async def list_top_scoring_candidates(
    resource_uids: list[UUIDy],
    only_with_term: bool = False,  # noqa: FBT001, FBT002
    order_by=OrderBy(),
    limit: int = 100,
    exclude_sent_ids: list[UUIDy] | None = None,
) -> list[UUID]:
    """スコアの上位から候補を出す."""
    rows = await search_tanbun_ids(
        "",
        paging=Paging(size=limit),
        order_by=order_by,
        belong_resource_uids=[to_uuid(u).hex for u in resource_uids],
        only_with_term=only_with_term,
        exclude_sent_ids=exclude_sent_ids,
    )
    return await filter_defined_sentence_ids(rows)
