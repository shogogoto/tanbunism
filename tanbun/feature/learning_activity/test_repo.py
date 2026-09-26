"""統合学習活動のrepoテスト."""

from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aregister

from .repo import fetch_learning_activity_counts


@mark_async_test()
async def test_fetch_learning_activity_counts() -> None:
    """知識量・クイズ作成・回答・正解を横断集計する."""
    user = await aregister("learning-activity@example.com")
    await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_id})
        CREATE
            (resource:Resource {
                uid: "activity-resource",
                name: "resource",
                resource_key: $resource_key
            }),
            (stat:ResourceStatsCache {n_sentence: 12}),
            (quiz1:Quiz {uid: "activity-quiz-1"}),
            (quiz2:Quiz {uid: "activity-quiz-2"}),
            (correct:Answer {uid: "activity-answer-1", is_correct: true}),
            (incorrect:Answer {uid: "activity-answer-2", is_correct: false}),
            (resource)-[:OWNED]->(user),
            (resource)-[:STATS]->(stat),
            (user)-[:CREATE]->(quiz1),
            (user)-[:CREATE]->(quiz2),
            (user)-[:ANSWER]->(correct),
            (user)-[:ANSWER]->(incorrect)
        """,
        params={
            "user_id": user.uid,
            "resource_key": f"{user.uid}:resource",
        },
    )

    result = await fetch_learning_activity_counts(user.uid)

    assert result.model_dump() == {
        "n_sentence": 12,
        "n_quiz_created": 2,
        "n_quiz_answered": 2,
        "n_quiz_correct": 1,
    }
