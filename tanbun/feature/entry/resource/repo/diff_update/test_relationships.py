"""関係だけの更新は、小さいクエリで既存IDを維持する。."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from lark import Token
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.entry.namespace import fetch_namespace
from tanbun.feature.entry.resource.repo.diff_update.cypher import n2label
from tanbun.feature.entry.resource.repo.diff_update.relationships import (
    RELATION_BATCH_SIZE,
    update_relationships,
)
from tanbun.feature.entry.resource.repo.diff_update.repo import (
    prepare_resource_diff,
    update_resource_diff,
)
from tanbun.feature.entry.resource.repo.restore import restore_sysnet
from tanbun.feature.entry.resource.usecase import save_resource_with_detail
from tanbun.feature.parsing.primitive.quoterm.domain import Quoterm
from tanbun.feature.parsing.tree2net import parse2net
from tanbun.feature.user.label import LUser


def test_labels_for_quoted_and_heading_nodes():
    """引用と見出しのラベルを区別する."""
    assert n2label(Quoterm(n="`A`")) == "Quoterm"
    assert n2label(Token("H2", "## heading")) == "Head"


@mark_async_test()
async def test_relationship_query_size_does_not_grow_with_edge_count(monkeypatch):
    """大量の関係もクエリ文字列を増やさず500件以下のパラメーターへ分割する。."""
    query = AsyncMock()
    monkeypatch.setattr(adb, "cypher_query", query)
    count = RELATION_BATCH_SIZE * 2 + 1
    uids = {str(i): uuid4() for i in range(count + 1)}
    edges = [(str(i), str(i + 1), EdgeType.ANTI) for i in range(count)]
    await update_relationships([], edges, uids, "root", {})
    batches = [call.args[1]["edges"] for call in query.call_args_list]
    assert [len(batch) for batch in batches] == [
        RELATION_BATCH_SIZE,
        RELATION_BATCH_SIZE,
        1,
    ]
    assert sum(map(len, batches)) == count
    assert len({call.args[0] for call in query.call_args_list}) == 1
    assert all("IN TRANSACTIONS" not in call.args[0] for call in query.call_args_list)


@mark_async_test()
async def test_quoted_relationships_restore_and_reimport_without_churn():
    """引用ノードに接続した関係を復元し、変化がなければ維持する。."""
    user = await LUser(email="relations-import@example.com").save()
    text = """# relations
  A: alpha
  B: beta
  context
    <- `A`
    <-> `B`
"""
    resource, _, _ = await save_resource_with_detail(
        await fetch_namespace(user.uid),
        text,
    )
    restored, before_uids = await restore_sysnet(resource.uid)
    assert any(isinstance(end, Quoterm) for _, end, _ in restored.g.edges(data=True))
    plan = await prepare_resource_diff(resource.uid, parse2net(text))
    assert not plan.removed_edges
    assert not plan.added_edges
    await update_resource_diff(resource.uid, parse2net(text), plan=plan)
    _, after_uids = await restore_sysnet(resource.uid)
    assert after_uids == before_uids


@mark_async_test()
async def test_failed_relationship_batch_rolls_back_all_changes(monkeypatch):
    """途中のバッチ失敗で、先に行った追加・削除もロールバックされる."""
    uids = {name: uuid4() for name in ("a", "b")}
    await adb.cypher_query(
        "CREATE (:Sentence {uid:$a})-[:ANTI]->(:Sentence {uid:$b})",
        {key: value.hex for key, value in uids.items()},
    )
    original = adb.cypher_query

    async def fail_second_addition(query, *args, **kwargs):
        if "CREATE (start)-[:TO]" in query:
            msg = "batch failed"
            raise RuntimeError(msg)
        return await original(query, *args, **kwargs)

    monkeypatch.setattr(adb, "cypher_query", fail_second_addition)
    with pytest.raises(RuntimeError, match="batch failed"):
        await update_relationships(
            [("a", "b", EdgeType.ANTI)],
            [("a", "b", EdgeType.EXAMPLE), ("a", "b", EdgeType.TO)],
            uids,
            "root",
            {},
        )
    rows, _ = await original("MATCH ()-[r]->() RETURN type(r)")
    assert rows == [["ANTI"]]
