"""既存ID間の関係だけを、短いパラメーター付きCypherで更新する。."""

from collections import defaultdict
from collections.abc import Iterable
from itertools import batched
from uuid import UUID

from neomodel import adb

from tanbun.config.transactions import atomic
from tanbun.feature.entry.resource.repo.diff_update.cypher import n2label
from tanbun.feature.entry.resource.repo.save import EdgeRel
from tanbun.feature.parsing.sysnet.sysnode import KNode, Sentency

RELATION_BATCH_SIZE = 500


async def update_relationships(
    removed: Iterable[EdgeRel],
    added: Iterable[EdgeRel],
    uids: dict[KNode, UUID],
    root: KNode,
    updated_sentences: dict[Sentency, Sentency],
) -> None:
    """HTTPの既存transactionへ参加し、単独呼び出しでも全バッチを一括確定する."""
    async with atomic():
        await _write_relationships(removed, added, uids, root, updated_sentences)


async def _write_relationships(
    removed: Iterable[EdgeRel],
    added: Iterable[EdgeRel],
    uids: dict[KNode, UUID],
    root: KNode,
    updated_sentences: dict[Sentency, Sentency],
) -> None:
    """ラベル・関係の種類別に定型クエリへまとめる."""
    new_to_old = {new: old for old, new in updated_sentences.items()}
    for edges, creating in [(removed, False), (added, True)]:
        groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        for edge_start, edge_end, relation in edges:
            start = new_to_old.get(edge_start, edge_start) if creating else edge_start
            end = new_to_old.get(edge_end, edge_end) if creating else edge_end
            labels = (
                "Resource" if start == root else n2label(start),
                "Resource" if end == root else n2label(end),
                relation.arrow,
            )
            groups[labels].append({"start": uids[start].hex, "end": uids[end].hex})
        for (start_label, end_label, relation), rows in sorted(groups.items()):
            operation = (
                f"CREATE (start)-[:{relation}]->(end)"
                if creating
                else f"MATCH (start)-[r:{relation}]->(end) DELETE r"
            )
            query = f"""
                UNWIND $edges AS edge
                MATCH (start:{start_label} {{uid: edge.start}})
                MATCH (end:{end_label} {{uid: edge.end}})
                {operation}
            """
            for batch in batched(rows, RELATION_BATCH_SIZE):
                await adb.cypher_query(query, {"edges": list(batch)})
