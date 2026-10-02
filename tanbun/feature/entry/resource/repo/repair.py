"""欠損したResource本文構造の修復."""

from collections import defaultdict
from uuid import UUID

from lark import Token
from neomodel import adb

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable, UUIDy, to_uuid
from tanbun.feature.entry.resource.repo.save import q_create_node, rel2q
from tanbun.feature.parsing.primitive.quoterm.domain import Quoterm
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import KNode


def _node_key(node: KNode) -> tuple[type, str]:
    """新旧の重複可能ノードを対応させるキー."""
    kind = type(node) if isinstance(node, (Duplicable, Quoterm)) else str
    return kind, str(node)


async def rebuild_resource_structure(
    resource_id: UUIDy,
    current: SysNet,
    current_uids: dict[KNode, UUID],
    source: SysNet,
) -> None:
    """単文のuidを維持して見出しと本文の階層関係を再生成."""
    resource_uid = to_uuid(resource_id)
    structural_edges = [
        (start, end, attr["type"])
        for start, end, attr in source.g.edges(data=True)
        if attr["type"] in {EdgeType.BELOW, EdgeType.SIBLING}
    ]
    structural_nodes = {node for edge in structural_edges for node in edge[:2]} - {
        source.root,
    }
    headings = {
        node
        for node in structural_nodes
        if isinstance(node, Token) and node.type.startswith("H")
    }

    uid_pools: dict[tuple[type, str], list[UUID]] = defaultdict(list)
    for node, uid in current_uids.items():
        if node != current.root and not isinstance(node, Token):
            uid_pools[_node_key(node)].append(uid)

    varnames: dict[KNode, str] = {source.root: "root"}
    matches: list[str] = []
    for index, node in enumerate(structural_nodes - headings):
        pool = uid_pools[_node_key(node)]
        if not pool:
            msg = f"既存単文を本文構造に対応できません: {node}"
            raise ValueError(msg)
        uid = pool.pop()
        var = f"existing_{index}"
        varnames[node] = var
        matches.append(f"MATCH ({var} {{uid: '{uid.hex}'}})")
    for index, heading in enumerate(headings):
        varnames[heading] = f"heading_{index}"

    await adb.cypher_query(
        """
        MATCH (root:Resource {uid: $uid})-[relation:BELOW]->()
        DELETE relation
        """,
        params={"uid": resource_uid.hex},
    )
    await adb.cypher_query(
        """
        MATCH (owned:Sentence|Quoterm {resource_uid: $uid})
            -[relation:BELOW|SIBLING]->()
        DELETE relation
        """,
        params={"uid": resource_uid.hex},
    )
    await adb.cypher_query(
        """
        MATCH (head:Head)-[:BELOW|SIBLING*]->
            (:Sentence|Quoterm {resource_uid: $uid})
        WITH DISTINCT head
        DETACH DELETE head
        """,
        params={"uid": resource_uid.hex},
    )

    creates = [q_create_node(heading, varnames) for heading in headings]
    relations = [rel2q(edge, varnames) for edge in structural_edges]
    query = "\n".join(
        [
            "MATCH (root:Resource {uid: $uid})",
            *matches,
            *[clause for clause in creates if isinstance(clause, str)],
            *[clause for clause in relations if isinstance(clause, str)],
        ],
    )
    await adb.cypher_query(query, params={"uid": resource_uid.hex})
