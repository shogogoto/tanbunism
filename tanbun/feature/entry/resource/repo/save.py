"""save sysnet."""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import networkx as nx
from lark import Token
from more_itertools import collapse
from neomodel import AsyncStructuredNode, adb
from pydantic import BaseModel

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.domain.types import Duplicable, to_uuid
from tanbun.feature.entry.label import LHead, LResource
from tanbun.feature.parsing.primitive.quoterm.domain import Quoterm
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.time import WhenNode

if TYPE_CHECKING:
    from tanbun.feature.domain.types import UUIDy
    from tanbun.feature.parsing.sysnet import SysNet
    from tanbun.feature.parsing.sysnet.sysnode import KNode


def val2str(val: Any) -> str:
    """値をcypher用文字列へ."""
    match val:
        case list():
            s = ", ".join([val2str(v) for v in val])
            return f"[{s}]"
        case date():
            return f"date('{val}')"
        case float() | int():
            return str(val)
        case UUID():
            return val.hex
        case _:
            return cypher_string(val)


def cypher_string(value: object) -> str:
    """Cypherへ埋め込む文字列リテラルを安全に作る."""
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def propstr(tgt: AsyncStructuredNode | BaseModel | dict) -> str:
    """Neomodel のラベルからcypher用プロパティ文字列へ."""
    d = tgt
    if isinstance(tgt, AsyncStructuredNode):
        d = tgt.__properties__
    if isinstance(tgt, BaseModel):
        d = tgt.model_dump(mode="json")

    d["uid"] = to_uuid(d["uid"]).hex
    kvs = [f"{k}: {val2str(v)}" for k, v in dict(d).items() if v]
    s = ", ".join(kvs)
    return f"{{ {s} }}"


def t2labels(t: type[AsyncStructuredNode]) -> str:
    """Convert to query string from neomodel type."""
    return ":".join(t.inherited_labels())


def q_create_node(
    n: KNode,
    nvars: dict[KNode, str],
) -> str | list[str] | None:
    """nodeからcreate可能な文字列に変換."""
    var = nvars.get(n)
    if var is None:
        raise ValueError

    match n:
        case Token() if n.type == "H1":
            pass
        case Token() if n.type.startswith("H"):  # heading
            uid = getattr(n, "uid", uuid4()).hex
            return (
                f"CREATE ({var}:{t2labels(LHead)} "
                f"{{val: {cypher_string(n)}, uid: '{uid}'}})"
            )
        case Term():
            ret = []
            for i, name in enumerate(n.names):
                ivar = f"{var}_{i}" if i > 0 else var
                c = f"CREATE ({ivar}:Term {{val: {cypher_string(name)}}})"
                ret.append(c)
            return ret
        case WhenNode():
            d = n.model_dump(mode="json")
            d["val"] = d.pop("n")
            return f"CREATE ({var}:Interval {propstr(d)})"
        case str() | Duplicable() | Quoterm():
            t = "Quoterm" if isinstance(n, Quoterm) else "Sentence"
            uid = getattr(n, "uid", uuid4()).hex
            return (
                f"CREATE ({var}:{t} "
                f"{{val: {cypher_string(n)}, uid: '{uid}', resource_uid: $uid}})"
            )
        case _:
            return None
    return None


def reconnect_root_below(sn: SysNet, varnames: dict[KNode, str]) -> str | None:
    """Resource infoを除外したときにbelowが途切れるのを防ぐ."""
    nodes = set(sn.meta)
    vs = set()
    for n in nodes:
        uvs = sn.g.edges(n)
        vs.update(uv[1] for uv in uvs)
    belows = list(vs - nodes)
    match len(belows):
        case 0:
            return None
        case 1:
            r = varnames[sn.root]
            b = varnames[belows[0]]
            return f"CREATE ({r}) -[:{EdgeType.BELOW.arrow}]-> ({b})"
        case _:
            msg = "reconnect_root_below error"
            raise ValueError(msg)


type EdgeRel = tuple[KNode, KNode, EdgeType]


def rel2q(
    edge: EdgeRel,
    varnames: dict[KNode, str],
) -> str | list[str] | None:
    """edgeからcreate可能な文字列に変換."""
    u, v, t = edge
    match t:
        case EdgeType.DEF:
            if not isinstance(u, Term):
                raise TypeError
            uv = varnames[u]
            p = f"{{ alias: {cypher_string(u.alias)} }}" if u.alias else ""
            ret = [f"CREATE ({uv}) -[:{t.arrow} {p}]-> ({varnames[v]})"]  # DEF
            names = [f"{uv}_{i}" if i > 0 else uv for i, name in enumerate(u.names)]
            # 別名は :ALIAS 関係 term同士の関係
            ret += [f"CREATE ({x}) -[:ALIAS]-> ({y})" for x, y in pairwise(names)]
            return ret
        case _:
            return f"CREATE ({varnames[u]}) -[:{t.arrow}]-> ({varnames[v]})"


def sysnet2cypher(sn: SysNet) -> str:
    """sysnetからnodeとrelのcreate文を順次作成."""
    nodes = sn.g.nodes - set(sn.meta)
    varnames = {n: f"n{i}" for i, n in enumerate(nodes)}
    root_var = "root"
    varnames[sn.root] = root_var
    q_root = [f"MATCH ({root_var}:{t2labels(LResource)} {{uid: $uid}})"]
    q_create = q_root + [q_create_node(n, varnames) for n in nodes]
    g = nx.subgraph_view(
        sn.g,
        filter_node=lambda n: n not in sn.meta,
    )
    q_rel = [rel2q((u, v, attr["type"]), varnames) for u, v, attr in g.edges.data()]
    q_rel.append(reconnect_root_below(sn, varnames))
    qs = collapse([*q_create, *q_rel])
    return "\n".join([q for q in qs if q is not None])


async def sn2db(sn: SysNet, resource_id: UUIDy, do_print: bool = False) -> None:  # noqa: FBT001, FBT002
    """新規登録."""
    q = sysnet2cypher(sn)
    if len(q.splitlines()) <= 1:  # create対象なし
        return
    if do_print:
        print()  # noqa: T201
        print(q)  # noqa: T201
    await adb.cypher_query(q, params={"uid": to_uuid(resource_id).hex})


async def has_persisted_root(resource_id: UUIDy) -> bool:
    """Resourceから本文への永続化された接続があるか."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (root:Resource {uid: $uid})
        RETURN EXISTS { MATCH (root)-[:BELOW]->() }
        """,
        params={"uid": to_uuid(resource_id).hex},
    )
    return bool(rows and rows[0][0])


async def repair_persisted_root(resource_id: UUIDy) -> bool:
    """Resourceから切れた最上位の本文へ一時的に再接続."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (root:Resource {uid: $uid})
        WHERE NOT EXISTS { MATCH (root)-[:BELOW]->() }
        MATCH (anchor:Sentence {resource_uid: $uid})
        MATCH (top:Head|Sentence)-[:BELOW|SIBLING*0..]->(anchor)
        WHERE NOT EXISTS {
            MATCH (:Head|Sentence)-[:BELOW|SIBLING]->(top)
        }
        WITH root, top, count(DISTINCT anchor) AS descendants
        ORDER BY descendants DESC
        LIMIT 1
        MERGE (root)-[:BELOW]->(top)
        RETURN top
        """,
        params={"uid": to_uuid(resource_id).hex},
    )
    return bool(rows)
