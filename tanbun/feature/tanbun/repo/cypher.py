"""cypherの組立て."""

from collections.abc import Callable
from textwrap import indent
from typing import Any, Final

from more_itertools import first_true
from neo4j.graph import Path

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import UUIDy
from tanbun.feature.entry.mapper import MResource
from tanbun.feature.repo.cypher import q_call_term_names
from tanbun.feature.tanbun.domain import LocationWithoutParents, UidStr
from tanbun.feature.tanbun.repo.adj import AdjType
from tanbun.feature.tanbun.repo.clause import OrderBy, WherePhrase
from tanbun.feature.user.public_schema import UserReadPublic


def q_indent(f: Callable) -> Callable:
    """インデントデコレータ."""

    def wrapper(*args, **kwargs):
        return indent(f(*args, **kwargs), " " * 2)

    return wrapper


def q_leaf_path(tgt: str, var: str, t: str) -> str:
    """ターゲット方向へのパス."""
    return f"""
        OPTIONAL MATCH {var} = ({tgt}:Sentence)
            -[rel_{var}:{t}]->{{1,}}(leaf_{var}:Sentence)
            WHERE NOT (leaf_{var}:Sentence)-[:TO]->(:Sentence)"""


def q_root_path(tgt: str, var: str, t: str) -> str:
    """ソース方向へのパス."""
    return f"""
        OPTIONAL MATCH {var} = (axiom_{var}:Sentence)-[:{t}]->{{1,}}({tgt})
            WHERE NOT (:Sentence)-[:TO]->(axiom_{var}:Sentence)"""


def q_stats(tgt: str, order_by: OrderBy | None = None) -> str:
    """関係統計の取得cypher."""
    # // {q_leaf_path(tgt, "p_leaf", EdgeType.TO.name)}
    # // {q_root_path(tgt, "p_axiom", EdgeType.TO.name)}
    # {q_adjacency_uids("tgts", all_chain=True)}
    return f"""
        // q_stats
        CALL ({tgt}) {{
            OPTIONAL MATCH p = ({tgt})<-[:QUOTERM]-(qt: Quoterm)
            WITH COLLECT(qt) AS qts, {tgt}
            UNWIND [{tgt}] + qts AS tgts
            WITH DISTINCT tgts
                , {tgt}

            {q_adjacency_uids("tgts", tgt)}
            WITH
              SIZE(premises) AS n_premise
            , SIZE(conclusions) AS n_conclusion
            //, MAX(coalesce(length(p_axiom), 0)) AS dist_axiom
            //, MAX(coalesce(length(p_leaf), 0)) AS dist_leaf
            , SIZE(referreds) AS n_referred
            , SIZE(refers) AS n_refer
            , SIZE(details) AS n_detail
            , SIZE(abstracts) AS n_abstract
            , SIZE(examples) AS n_example
            RETURN {{
                n_premise: n_premise
                , n_conclusion: n_conclusion
                // , dist_axiom: dist_axiom
                // , dist_leaf: dist_leaf
                , n_referred: n_referred
                , n_refer: n_refer
                , n_detail: n_detail
                , n_abstract: n_abstract
                , n_example: n_example
                {(order_by.score_prop() if order_by else "")}
            }} AS stats
        }}
        """


def q_chain(var: str, et: EdgeType, indent_len: int = 0) -> str:
    """同一関係パスによるstart,end,type."""
    s = f"""
        // {et.name} Chain
        MATCH (:Sentence|Quoterm)-[r:{et.name}]-(:Sentence|Quoterm)
            -[:{et.name}]-*({var})
        RETURN startNode(r) as start, endNode(r) as end, type(r) as type
    """
    return indent(s, " " * indent_len)


def q_where_tanbun(p: WherePhrase = WherePhrase.CONTAINS) -> str:
    """検索文字列が含まれている文と用語に紐づく文を返す."""
    where_phrase = f"{p.value} $s"
    return f"""
        // 検索文字列が含まれる文 q_where_tanbun
        MATCH (sent1: Sentence WHERE sent1.val {where_phrase})
        {q_call_term_names("sent1")}
        RETURN sent1 AS sent
        UNION
        // 検索文字列が含まれる用語
        MATCH (term2: Term WHERE term2.val {where_phrase}),
        (n1)-[:ALIAS]-*(term2: Term)-[:ALIAS]-*(n2: Term)
            -[:DEF]->(sent3: Sentence)
        UNWIND [n2, n1] AS name3
        RETURN sent3 AS sent
    """


def q_search(
    where: WherePhrase,
    filter_resource_uids: list[UUIDy] | None,
    only_with_term: bool,  # noqa: FBT001
    exclude_sent_ids: list[UUIDy] | None = None,
) -> str:
    """search_tanbunとtotalのクエリ."""
    q_term = "MATCH (sent)<-[:DEF]-(:Term)" if only_with_term else ""
    # フィルタ条件の構築
    conditions = []
    if filter_resource_uids:
        conditions.append("sent.resource_uid IN $resource_uids")
    if exclude_sent_ids:
        conditions.append("NOT sent.uid IN $exclude_uids")  # 除外条件

    q_where = ""
    if conditions:
        q_where = "WHERE " + " AND ".join(conditions)

    return rf"""
        CALL () {{
        {indent(q_where_tanbun(where), " " * 4)}
        }}
        {q_term}
        WITH sent
        {q_where}
        """


def q_adjacency_uids(
    sent_var: str,
    aggregate_var: str,
    dist: int | None = None,
    types: list[AdjType] | None = None,
) -> str:
    """隣接する文のIDを返す."""
    if types is None:
        types = AdjType.location_types()

    match_clauses = [t.match(sent_var, dist) for t in types]
    collect_clauses = [t.collect for t in types]
    return f"""
        {"".join(match_clauses)}
        WITH
            {aggregate_var} // ここで集計する単位が決まる
            {"".join(collect_clauses)}
    """


# resourceに至るには SIBLING|BELOW|HEAD(|NUM) のみを辿れば良い
#  これをstream と呼ぶ
# これ以外では 無方向でアスタによる高コストな 複雑関係 EXAMPLE|TO|RESOLVED
#   で対称のsentenceまでのpathを取得する
# この complex の端以外では stream は現れない、として曖昧さ・検索コストを減らす
#  (r:Resource)-[stream]->*(:Sentence)-[complex]-(:Sentence)
# (:Resource)--*(sent) ではコスト高すぎるかも
#

STREAM: Final = "SIBLING|BELOW|NUM|BY"


def q_upper(sent_var: str) -> str:
    """parentの末尾 upper を取得する."""
    # RESOLVED は含めない ブロックを飛び越えて広範囲に探索することになって
    #   応答が返ってこなくなる
    complex_ = "TO|EXAMPLE"  # resourceに近づくとは限らない方向
    return f"""
        CALL ({sent_var}) {{
            // Resource直下でも許容
            MATCH (r:Resource {{uid: {sent_var}.resource_uid}})
            OPTIONAL MATCH p = (r)-[:{STREAM}]->*
                (_upper:Sentence|Head)-[:{STREAM}]->
                (up:Sentence)
                , (up)-[:{complex_}|NUM|BY]-*({sent_var})
            WITH p, LENGTH(p) as len, up, _upper, r
            ORDER BY len ASC // 最短
            LIMIT 1
            RETURN CASE
                WHEN up IS NOT NULL THEN up
                WHEN _upper IS NOT NULL THEN _upper
                ELSE {sent_var}
            END AS upper
                , r AS resource
        }}
    """


def q_location(sent_var: str) -> str:
    """位置情報."""
    return f"""
    CALL ({sent_var}) {{
        {q_upper(sent_var)}
        OPTIONAL MATCH p2 = (resource)-[:{STREAM}]->*(upper)
        , p = (user:User)<-[:OWNED|PARENT]-*(resource)
        RETURN nodes(p) + p2 AS location
    }}
    """


def q_quote_locations() -> str:
    """引用用語が置かれた各場所の位置情報."""
    return f"""
        UNWIND $uids AS uid
        MATCH (sent:Sentence {{uid: uid}})<-[:QUOTERM]-(quote:Quoterm)
        MATCH (resource:Resource {{uid: quote.resource_uid}})
        MATCH quote_path = (resource)-[:{STREAM}]->*(quote)
            , owner_path = (user:User)<-[:OWNED|PARENT]-*(resource)
        RETURN sent.uid, quote.uid, nodes(owner_path) + quote_path AS location
        ORDER BY quote.uid
    """


def build_location_res(
    row: Any,
    self_uid: str,
) -> tuple[LocationWithoutParents, list[str]]:
    """locationのレコードからmodelを組み立てる."""
    row = list(dict.fromkeys(row))
    user = UserReadPublic.model_validate(dict(row[0]), by_alias=True)
    r = first_true(row[1:], pred=lambda n: "Resource" in n.labels)
    i_r = row.index(r)

    path: Path = row[i_r + 1]  # リソース ~ 文のパス
    heads = [
        rel.end_node for rel in path.relationships if "Head" in rel.end_node.labels
    ]
    headers = [UidStr(val=e.get("val"), uid=e.get("uid")) for e in heads]
    parent_uids = [
        rel.start_node.get("uid")
        for rel in path.relationships
        if rel.type == "BELOW" and "Sentence" in rel.start_node.labels
    ]
    if self_uid in parent_uids:
        parent_uids.remove(self_uid)
    return LocationWithoutParents(
        user=user,
        folders=[UidStr(val=e.get("name"), uid=e.get("uid")) for e in row[1:i_r]],
        resource=MResource.freeze_dict(dict(r)),
        headers=headers,
    ), list(dict.fromkeys(parent_uids))
