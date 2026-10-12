"""PageRank ordering of newly selected dungeon quiz populations."""

from unittest.mock import AsyncMock
from uuid import uuid4

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
