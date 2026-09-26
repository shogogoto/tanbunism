"""既存の学習データから活動量を復元するrepo."""

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid

from .domain import LearningActivityCounts


async def fetch_learning_activity_counts(
    user_id: UUIDy,
) -> LearningActivityCounts:
    """知識量とクイズ活動を横断して集計する."""
    uid = to_uuid(user_id).hex
    knowledge_rows, _ = await adb.cypher_query(
        """
        MATCH (resource:Resource)-[:STATS]->(stat:ResourceStatsCache)
        WHERE resource.resource_key STARTS WITH $resource_key_prefix
        RETURN coalesce(sum(stat.n_sentence), 0)
        """,
        params={"resource_key_prefix": f"{uid}:"},
    )
    quiz_rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_id})
        OPTIONAL MATCH (user)-[:CREATE]->(quiz:Quiz)
        WITH user, count(quiz) AS n_quiz_created
        OPTIONAL MATCH (user)-[:ANSWER]->(answer:Answer)
        RETURN n_quiz_created,
            count(answer) AS n_quiz_answered,
            count(CASE WHEN answer.is_correct THEN 1 END) AS n_quiz_correct
        """,
        params={"user_id": uid},
    )
    n_sentence = knowledge_rows[0][0] if knowledge_rows else 0
    quiz_counts = quiz_rows[0] if quiz_rows else (0, 0, 0)
    return LearningActivityCounts(
        n_sentence=n_sentence,
        n_quiz_created=quiz_counts[0],
        n_quiz_answered=quiz_counts[1],
        n_quiz_correct=quiz_counts[2],
    )
