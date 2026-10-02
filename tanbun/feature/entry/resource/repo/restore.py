"""db から sysnetを復元."""

from functools import cache
from math import isinf
from uuid import UUID

import neo4j
import networkx as nx
from lark import Token
from neomodel.async_.core import AsyncDatabase

from tanbun.feature.domain.errors import NotFoundError
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable, UUIDy, is_duplicable, to_uuid
from tanbun.feature.entry.label import LResource
from tanbun.feature.parsing.primitive.quoterm.domain import Quoterm
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.time import WhenNode
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import (
    DUMMY_SENTENCE,
    DummySentence,
    KNode,
    add_def_edge,
)
from tanbun.feature.repo.cypher import q_call_term_names


@cache
def to_tanbun(n: neo4j.graph.Node) -> KNode:  # noqa: PLR0911
    """neo4jから変換."""
    lb_name = next(iter(n.labels))
    val = n.get("val")
    match lb_name:
        case "Head":
            return Token(type="H2", value=val)  # 適当なheading type
        case "Sentence":
            if is_duplicable(val):
                return Duplicable(n=val, uid=n.get("uid"))
            return DummySentence(uid=n.get("uid")) if val == DUMMY_SENTENCE else val
        case "Term":
            return Term.create(val)
        case "Resource" | "Entry":
            return Token(type="H1", value=n.get("title"))
        case "Interval":
            d = dict(n)
            d["n"] = d.pop("val")
            for k in ("start", "end"):  # infはJSONに変換できない
                if k not in d or isinf(d[k]):
                    d[k] = None
            return WhenNode.model_validate(d)
        case "Quoterm":
            return Quoterm(n=val, uid=n.get("uid"))
        case _:
            props = n.items()
            raise ValueError(props, lb_name)


async def restore_tops(resource_uid: UUIDy) -> tuple[nx.DiGraph, dict[UUID, KNode]]:
    """SysNetを先のHeadまたはSentenceまで復元."""
    ruid = to_uuid(resource_uid)
    rsc = await LResource.nodes.get_or_none(uid=ruid.hex)
    if rsc is None:
        msg = f"リソースが見つかりません: {ruid}"
        raise NotFoundError(msg)
    q = """
        MATCH (root:Resource {uid: $uid})
        OPTIONAL MATCH (root)-[:BELOW|SIBLING*]->
            (s:Head)-[r:BELOW|SIBLING]->(e:Head|Sentence|Quoterm)
        WHERE r IS NOT NULL AND e IS NOT NULL AND root IS NOT NULL
        RETURN r, s, e
        UNION
        MATCH (root:Resource {uid: $uid})
        OPTIONAL MATCH (root)-[r:BELOW]->(e:Head|Sentence|Quoterm)
        WHERE r IS NOT NULL AND e IS NOT NULL AND root IS NOT NULL
        RETURN r, root as s, e
    """
    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={"uid": ruid.hex},
    )
    g = nx.MultiDiGraph()
    # 詳細が空の場合に何も返さないのを阻止
    title = Token(type="H1", value=rsc.title)
    g.add_node(ruid)
    uids: dict[UUID, KNode] = {ruid: title}
    has_root_edge = False
    for row in rows:
        r, s_, e_ = row
        if r is None:
            continue
        has_root_edge = True
        suid = to_uuid(s_.get("uid"))
        euid = to_uuid(e_.get("uid"))
        uids[suid] = to_tanbun(s_)
        uids[euid] = to_tanbun(e_)
        EdgeType(r.type.lower()).add_edge(g, suid, euid)
    if not has_root_edge:
        await _restore_detached_tops(ruid, g, uids)
    return g, uids


async def _restore_detached_tops(
    resource_uid: UUID,
    graph: nx.MultiDiGraph,
    uids: dict[UUID, KNode],
) -> None:
    """Resourceとの接続を失った最上位の本文ツリーを復元."""
    q = """
        MATCH (anchor:Sentence {resource_uid: $uid})
        MATCH (top:Head|Sentence)-[:BELOW|SIBLING*0..]->(anchor)
        WHERE NOT EXISTS {
            MATCH (:Head|Sentence)-[:BELOW|SIBLING]->(top)
        }
        WITH top, count(DISTINCT anchor) AS descendants
        ORDER BY descendants DESC
        LIMIT 1
        OPTIONAL MATCH (top)-[:BELOW|SIBLING*0..]->(s:Head)
        OPTIONAL MATCH (s)-[r:BELOW|SIBLING]->(e:Head|Sentence|Quoterm)
        RETURN top, r, s, e
    """
    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={"uid": resource_uid.hex},
    )
    if not rows:
        return

    top = rows[0][0]
    top_uid = to_uuid(top.get("uid"))
    uids[top_uid] = to_tanbun(top)
    EdgeType.BELOW.add_edge(graph, resource_uid, top_uid)
    for _top, relation, start, end in rows:
        if relation is None:
            continue
        start_uid = to_uuid(start.get("uid"))
        end_uid = to_uuid(end.get("uid"))
        uids[start_uid] = to_tanbun(start)
        uids[end_uid] = to_tanbun(end)
        EdgeType(relation.type.lower()).add_edge(graph, start_uid, end_uid)


async def restore_undersentnet(  # noqa: PLR0914
    resource_uid: UUIDy,
) -> tuple[nx.DiGraph, dict[UUID, KNode], dict[UUID, Term]]:
    """top以下のsentenceやdefのネットワークを復元."""
    various = "|".join([et.name for et in EdgeType if et != EdgeType.DEF])
    q = f"""
        MATCH (s:Sentence|Quoterm {{resource_uid: $uid}})
        OPTIONAL MATCH (s)-[r:{various}]->(e:Sentence|Interval|Quterm)
        {q_call_term_names("s")}
        RETURN s
            , COLLECT([e, r]) as ends
            , COALESCE(names, []) as names
            , alias
    """
    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={"uid": to_uuid(resource_uid).hex},
    )
    g = nx.MultiDiGraph()
    uids: dict[UUID, KNode] = {}
    terms: dict[UUID, Term] = {}
    for row in rows:
        s, ends, names, alias = row
        names = [n.get("val") for n in names]
        sval = to_tanbun(s)
        suid = to_uuid(s.get("uid"))
        uids[suid] = sval
        g.add_node(suid)
        for end in ends:
            e, r = end
            if e is None:
                continue
            euid = to_uuid(e.get("uid"))
            EdgeType(r.type.lower()).add_edge(g, suid, euid)
            uids[euid] = to_tanbun(e)
        if len(names) > 0:
            term = Term.create(*names, alias=alias)
            terms[suid] = term
    return g, uids, terms


async def restore_graph(
    resource_uid: UUIDy,
) -> tuple[nx.DiGraph, dict[UUID, KNode], dict[UUID, Term]]:
    """SysNetのgraphを復元."""
    g1, uids1 = await restore_tops(resource_uid)
    g2, uids2, terms2 = await restore_undersentnet(resource_uid)
    g = nx.compose(g1, g2)
    uids = {**uids1, **uids2}
    return g, uids, terms2


def quoterm_sent_dict(g: nx.DiGraph, uids: dict[KNode, UUID]) -> dict[Quoterm, UUID]:
    """引用用語と単文uidの辞書."""
    d = {}
    for qt in [n for n in g.nodes if isinstance(n, Quoterm)]:
        sent = next(iter(EdgeType.QUOTERM.succ(g, qt)))
        d[qt] = uids[sent]
    return d


async def restore_sysnet(resource_uid: UUIDy) -> tuple[SysNet, dict[KNode, UUID]]:
    """SysNetを復元."""
    g, uids, terms = await restore_graph(resource_uid)
    for uid, term in terms.items():
        add_def_edge(g, uids[uid], term)
    g_relabeled = nx.relabel_nodes(g, uids)
    uid_reverse = {v: k for k, v in uids.items()}
    # quots = quoterm_sent_dict(g_relabeled, uid_reverse)
    title = str(uids[to_uuid(resource_uid)])
    return SysNet(g=g_relabeled, root=title), uid_reverse
