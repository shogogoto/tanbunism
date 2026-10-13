"""PageRank ordering of newly selected dungeon quiz populations."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.selection import repo as selection
from tanbun.feature.quiz.learning.selection.domain import (
    QuizTargetOrder,
    QuizTargetPool,
)

from .population import _available_quiz_ids, get_or_prepare_region_pool


@mark_async_test()
async def test_population_pagerank_order_and_fixed_prefix(monkeypatch):
    """New populations prioritize PageRank and preserve earlier selections."""
    user, resource = uuid4(), uuid4()
    ids = [uuid4().hex for _ in range(10)]
    await adb.cypher_query(
        """CREATE (user:User {uid:$user})
        CREATE (resource:Resource {uid:$resource, txt_hash:1, updated:1,
            pagerank_version:1, pagerank_source_hash:1, pagerank_source_updated:1})
        CREATE (plan:StudyPlan {auto_resource_uid:$resource, quiz_types:['sent2term']})
        CREATE (plan)-[:OWNED]->(user)
        WITH user UNWIND range(0,9) AS i
        CREATE (s:Sentence {uid:$ids[i], resource_uid:$resource,
            val:'sentence', pagerank_score:i})
        CREATE (q:Quiz {uid:$ids[i],quiz_type:'sent2term',created:i})
        CREATE (user)-[:LEARN]->(q)
        CREATE (q)-[:QUIZ_TARGET]->(s)""",
        {"user": user.hex, "resource": resource.hex, "ids": ids},
    )
    assert await _available_quiz_ids(user, resource, [], 5) == ids[::-1][:5]
    first = await get_or_prepare_region_pool(user, resource, 1)
    await adb.cypher_query(
        "MATCH (s:Sentence {uid:$id}) SET s.pagerank_score=100",
        {"id": ids[0]},
    )
    second = await get_or_prepare_region_pool(user, resource, 2)
    assert first["ready"]
    assert second["ready"]
    assert second["quiz_ids"][:5] == first["quiz_ids"]
    assert second["quiz_ids"][5] == ids[0]
    monkeypatch.setattr(
        selection,
        "fetch_uncovered_sent_ids",
        AsyncMock(return_value=ids),
    )
    targets = await selection.fetch_target_ids(
        resource,
        user,
        QuizType.SENT2TERM,
        QuizTargetPool.UNCOVERED,
        QuizTargetOrder.PAGERANK,
        3,
    )
    assert [target.hex for target in targets] == [ids[0], ids[9], ids[8]]


async def seed_prepared_quizzes(user, resource, ids):
    """実際の既定Planと同じ形式で準備済みクイズを保存する."""
    await adb.cypher_query(
        """CREATE (user:User {uid:$user})
        CREATE (resource:Resource {uid:$resource})
        CREATE (plan:StudyPlan {auto_resource_uid:$resource, quiz_types:['SENT2TERM']})
        CREATE (plan)-[:OWNED]->(user)
        WITH user UNWIND range(0,4) AS i
        CREATE (s:Sentence {uid:$ids[i], resource_uid:$resource})
        CREATE (q:Quiz {uid:$ids[i], quiz_type:'SENT2TERM', created:i})
        CREATE (user)-[:LEARN]->(q)
        CREATE (q)-[:QUIZ_TARGET]->(s)""",
        {"user": user.hex, "resource": resource.hex, "ids": [uid.hex for uid in ids]},
    )


@mark_async_test()
async def test_legacy_hyphenated_ids_migrate_to_canonical_population():
    """画面から送るハイフン付きUUIDでもDBのhex IDと照合できる."""
    user, resource = uuid4(), uuid4()
    ids = [uuid4() for _ in range(5)]
    await seed_prepared_quizzes(user, resource, ids)
    result = await get_or_prepare_region_pool(
        user,
        resource,
        1,
        [str(uid) for uid in ids],
    )
    assert result["ready"]
    assert result["quiz_ids"] == [uid.hex for uid in ids]


@pytest.mark.parametrize("invalid_id", ["deleted", "malformed", "foreign"])
@mark_async_test()
async def test_invalid_legacy_pool_falls_back_to_owned_prepared_quizzes(invalid_id):
    """古い候補が無効でも、本人の準備済みクイズを0問と誤判定しない."""
    user, resource = uuid4(), uuid4()
    ids = [uuid4() for _ in range(5)]
    await seed_prepared_quizzes(user, resource, ids)
    missing = uuid4().hex
    if invalid_id == "foreign":
        await adb.cypher_query(
            "CREATE (:Quiz {uid:$uid, quiz_type:'SENT2TERM'})",
            {"uid": missing},
        )
    legacy = [uid.hex for uid in ids[:4]] + [
        "not-a-uuid" if invalid_id == "malformed" else missing,
    ]
    result = await get_or_prepare_region_pool(user, resource, 1, legacy)
    assert result["ready"]
    assert result["available_quizzes"] == len(ids)
    assert result["quiz_ids"] == [uid.hex for uid in ids]
