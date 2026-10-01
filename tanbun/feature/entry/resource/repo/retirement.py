"""参照されている単文の退役処理."""

from collections.abc import Iterable

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.parsing.sysnet.sysnode import KNode, Sentency

QUIZ_SENTENCE_RELS = "QUIZ_TARGET|QUIZ_OPTION|CORRECT"


def retire_or_delete_sentence_qs(
    sentences: Iterable[Sentency],
    varnames: dict[KNode, str],
) -> list[str]:
    """クイズから参照される単文は退役させ、それ以外は削除する.

    Quizとの元の関係は、後から同じ単文へ再接続する際の判断材料として残す。
    利用可能なQuizかどうかは ``BROKEN_BY`` 関係の有無から判断する。
    """
    queries = []
    for sentence in sentences:
        var = varnames[sentence]
        result_var = f"retired_{var}"
        queries.append(
            f"""
            CALL ({var}, root) {{
                OPTIONAL MATCH (quiz:Quiz)-[:{QUIZ_SENTENCE_RELS}]->({var})
                WITH {var}, [q IN collect(DISTINCT quiz) WHERE q IS NOT NULL]
                    AS quizzes
                FOREACH (q IN quizzes |
                    MERGE (q)-[:BROKEN_BY]->({var})
                )
                FOREACH (_ IN CASE WHEN size(quizzes) > 0 THEN [1] ELSE [] END |
                    REMOVE {var}:Sentence
                    SET {var}:RetiredSentence,
                        {var}.retired_at = datetime(),
                        {var}.resource_name = root.title
                )
                FOREACH (_ IN CASE WHEN size(quizzes) = 0 THEN [1] ELSE [] END |
                    DETACH DELETE {var}
                )
                RETURN size(quizzes) AS {result_var}
            }}
            """.strip(),
        )
    return queries


async def retire_referenced_sentences(resource_uid: UUIDy) -> None:
    """Resource削除前に、Quizや回答から参照される単文だけを退役させる."""
    query = f"""
        MATCH (resource:Resource {{uid: $uid}})
        MATCH (sentence:Sentence {{resource_uid: $uid}})
        WHERE EXISTS {{
            MATCH (:Quiz)-[:{QUIZ_SENTENCE_RELS}]->(sentence)
        }} OR EXISTS {{
            MATCH (:Answer)-[:SELECT]->(sentence)
        }}
        OPTIONAL MATCH (quiz:Quiz)-[:{QUIZ_SENTENCE_RELS}]->(sentence)
        WITH resource, sentence,
            [item IN collect(DISTINCT quiz) WHERE item IS NOT NULL] AS quizzes
        FOREACH (quiz IN quizzes |
            MERGE (quiz)-[:BROKEN_BY]->(sentence)
        )
        REMOVE sentence:Sentence
        SET sentence:RetiredSentence,
            sentence.retired_at = datetime(),
            sentence.resource_name = resource.title
    """
    await adb.cypher_query(query, params={"uid": to_uuid(resource_uid).hex})


async def purge_orphaned_retired_sentences() -> int:
    """Quizや回答履歴から参照されなくなった退役単文を物理削除する."""
    query = f"""
        MATCH (retired:RetiredSentence)
        WHERE NOT EXISTS {{
            MATCH (:Quiz)-[:{QUIZ_SENTENCE_RELS}|BROKEN_BY]->(retired)
        }}
          AND NOT EXISTS {{
            MATCH (:Answer)-[:SELECT]->(retired)
        }}
        WITH collect(retired) AS orphans
        FOREACH (retired IN orphans | DETACH DELETE retired)
        RETURN size(orphans)
    """
    rows, _ = await adb.cypher_query(query)
    return rows[0][0]
