"""同一本文の強制再解析は、既存IDと学習データを保持して欠損関係を補う。."""

from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.entry.label import LResource
from tanbun.feature.entry.namespace import fetch_namespace
from tanbun.feature.entry.resource.repo.restore import restore_sysnet
from tanbun.feature.entry.resource.usecase import save_resource_with_detail
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.quiz.learning.fixture import answer_test_quiz
from tanbun.feature.user.label import LUser


@mark_async_test()
async def test_force_import_restores_anti_without_replacing_existing_data():
    """旧importで欠けたANTIだけを補い、単文・クイズ・回答履歴を維持する。."""
    user = await LUser(email="force-import@example.com").save()
    text = """# force import
  楽観: 未来を明るく見る
    <-> 悲観: 未来を暗く見る
  保留: 未来をまだ判断しない
"""
    resource, _, _ = await save_resource_with_detail(
        await fetch_namespace(user.uid),
        text,
    )
    _, original_uids = await restore_sysnet(resource.uid)
    quiz = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        original_uids["未来を明るく見る"],
        2,
        user.uid,
    )
    await answer_test_quiz(quiz, user.uid, correctly=True)
    await adb.cypher_query(
        "MATCH (:Sentence {resource_uid: $uid})-[r:ANTI]->() DELETE r",
        {"uid": resource.uid.hex},
    )
    cached = await (await LResource.nodes.get(uid=resource.uid.hex)).cached_stats.get()
    cached.n_sentence = 999
    await cached.save()

    _, _, skipped = await save_resource_with_detail(
        await fetch_namespace(user.uid),
        text,
    )
    incomplete, _ = await restore_sysnet(resource.uid)
    assert not skipped
    assert not list(EdgeType.ANTI.succ(incomplete.g, "未来を明るく見る"))

    repaired, _, changed = await save_resource_with_detail(
        await fetch_namespace(user.uid),
        text,
        force=True,
    )
    restored, restored_uids = await restore_sysnet(resource.uid)
    assert repaired.uid == resource.uid
    assert repaired.updated == resource.updated
    assert changed
    assert restored_uids == original_uids
    assert list(EdgeType.ANTI.succ(restored.g, "未来を明るく見る")) == [
        "未来を暗く見る",
    ]
    assert (await fetch_namespace(user.uid)).stats[resource.uid.hex].n_sentence == len(
        restored.sentences,
    )
    rows, _ = await adb.cypher_query(
        """
        MATCH (u:User {uid: $user})-[:LEARN]->(q:Quiz {uid: $quiz})
            -[:QUIZ_TARGET]->(s:Sentence {uid: $sentence})
        MATCH (u)-[:ANSWER]->(a:Answer)-[:ANSWER_OF]->(q)
        RETURN count(DISTINCT q), count(DISTINCT a)
        """,
        {
            "user": user.uid,
            "quiz": quiz.quiz_id.hex,
            "sentence": original_uids["未来を明るく見る"].hex,
        },
    )
    assert rows == [[1, 1]]
    _, _, repeated = await save_resource_with_detail(
        await fetch_namespace(user.uid),
        text,
        force=True,
    )
    assert repeated
    assert (await restore_sysnet(resource.uid))[1] == original_uids
