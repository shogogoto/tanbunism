"""孤立単文を監査・掃除するNeo4j操作."""

from neomodel import adb

from tanbun.feature.tanbun.repo.cypher import STREAM

from .domain import DeleteOrphanedTanbunsResult, OrphanedTanbun, OrphanReason

QUIZ_SENTENCE_RELS = "QUIZ_TARGET|QUIZ_OPTION|CORRECT"
LOCATION_RELS = f"{STREAM}"
SEMANTIC_LOCATION_RELS = "TO|EXAMPLE|NUM|BY"


def _orphan_predicate(sentence_var: str) -> str:
    """詳細画面でResource上の位置を解決できないSentenceを絞る."""
    return f"""
        NOT EXISTS {{
            MATCH (resource:Resource {{uid: {sentence_var}.resource_uid}})
            MATCH (resource)-[:PARENT|OWNED]->*(owner:User)
            MATCH (resource)-[:{LOCATION_RELS}]->*({sentence_var})
        }}
        AND NOT EXISTS {{
            MATCH (resource:Resource {{uid: {sentence_var}.resource_uid}})
            MATCH (resource)-[:PARENT|OWNED]->*(owner:User)
            MATCH (resource)-[:{LOCATION_RELS}]->*(upper:Sentence)
            MATCH (upper)-[:{SEMANTIC_LOCATION_RELS}]->*({sentence_var})
        }}
    """


async def list_orphaned_tanbuns(*, limit: int = 200) -> list[OrphanedTanbun]:
    """配置を解決できない現行Sentenceを参照状況付きで返す."""
    orphan_predicate = _orphan_predicate("sentence")
    query = f"""
        MATCH (sentence:Sentence)
        WHERE {orphan_predicate}
        OPTIONAL MATCH (resource:Resource {{uid: sentence.resource_uid}})
        OPTIONAL MATCH (resource)-[:PARENT|OWNED]->*(owner:User)
        CALL (sentence) {{
            OPTIONAL MATCH (quiz:Quiz)-[:{QUIZ_SENTENCE_RELS}]->(sentence)
            RETURN count(DISTINCT quiz) AS quiz_count
        }}
        CALL (sentence) {{
            OPTIONAL MATCH (answer:Answer)-[:SELECT]->(sentence)
            RETURN count(DISTINCT answer) AS answer_count
        }}
        CALL (sentence) {{
            OPTIONAL MATCH (sentence)-[relationship]-()
            RETURN count(DISTINCT relationship) AS relationship_count
        }}
        WITH sentence, resource, head(collect(DISTINCT owner)) AS owner,
            quiz_count, answer_count, relationship_count
        RETURN sentence.uid, sentence.val, sentence.resource_uid,
            resource.title, owner.email,
            CASE
                WHEN resource IS NULL THEN 'missing_resource'
                WHEN owner IS NULL THEN 'missing_owner'
                ELSE 'missing_location'
            END AS reason,
            quiz_count, answer_count, relationship_count
        ORDER BY quiz_count + answer_count DESC, sentence.val, sentence.uid
        LIMIT $limit
    """
    rows, _ = await adb.cypher_query(query, params={"limit": limit})
    return [
        OrphanedTanbun(
            uid=uid,
            sentence=sentence,
            resource_uid=resource_uid,
            resource_name=resource_name,
            owner_email=owner_email,
            reason=OrphanReason(reason),
            quiz_reference_count=quiz_count,
            answer_reference_count=answer_count,
            relationship_count=relationship_count,
        )
        for (
            uid,
            sentence,
            resource_uid,
            resource_name,
            owner_email,
            reason,
            quiz_count,
            answer_count,
            relationship_count,
        ) in rows
    ]


async def delete_orphaned_tanbuns(
    sentence_ids: list[str],
) -> DeleteOrphanedTanbunsResult:
    """まだ孤立している単文だけを削除し、履歴参照があれば退役させる."""
    orphan_predicate = _orphan_predicate("sentence")
    query = f"""
        UNWIND $sentence_ids AS sentence_id
        OPTIONAL MATCH (sentence:Sentence {{uid: sentence_id}})
        WHERE sentence IS NOT NULL AND {orphan_predicate}
        CALL (sentence) {{
            OPTIONAL MATCH (quiz:Quiz)-[:{QUIZ_SENTENCE_RELS}]->(sentence)
            RETURN [item IN collect(DISTINCT quiz) WHERE item IS NOT NULL]
                AS quizzes
        }}
        CALL (sentence) {{
            OPTIONAL MATCH (answer:Answer)-[:SELECT]->(sentence)
            RETURN count(DISTINCT answer) AS answer_count
        }}
        WITH sentence_id, sentence, quizzes, answer_count,
            size(quizzes) > 0 OR answer_count > 0 AS must_retire
        FOREACH (quiz IN CASE WHEN sentence IS NOT NULL THEN quizzes ELSE [] END |
            MERGE (quiz)-[:BROKEN_BY]->(sentence)
        )
        FOREACH (_ IN CASE
            WHEN sentence IS NOT NULL AND must_retire THEN [1] ELSE [] END |
            REMOVE sentence:Sentence
            SET sentence:RetiredSentence, sentence.retired_at = datetime()
        )
        FOREACH (_ IN CASE
            WHEN sentence IS NOT NULL AND NOT must_retire THEN [1] ELSE [] END |
            DETACH DELETE sentence
        )
        RETURN
            count(CASE WHEN sentence IS NOT NULL AND NOT must_retire THEN 1 END)
                AS deleted_count,
            count(CASE WHEN sentence IS NOT NULL AND must_retire THEN 1 END)
                AS retired_count,
            count(CASE WHEN sentence IS NULL THEN 1 END) AS skipped_count
    """
    rows, _ = await adb.cypher_query(
        query,
        params={"sentence_ids": list(dict.fromkeys(sentence_ids))},
    )
    deleted_count, retired_count, skipped_count = rows[0]
    return DeleteOrphanedTanbunsResult(
        deleted_count=deleted_count,
        retired_count=retired_count,
        skipped_count=skipped_count,
    )
