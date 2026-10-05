"""公開クイズ検索。単文の知識スコア順で返す."""

from uuid import UUID

from neomodel import adb
from pydantic import BaseModel

from tanbun.feature.quiz.domain.domain import ReadableQuiz
from tanbun.feature.quiz.repo.restore import restore_quiz_sources
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.tanbun.repo.clause import OrderBy
from tanbun.feature.tanbun.repo.cypher import q_stats


class QuizSearchItem(BaseModel):
    """公開クイズと対象・作成者の情報."""

    quiz: ReadableQuiz
    target_score: int
    resource_id: UUID
    resource_name: str
    creator_id: UUID
    creator_username: str | None = None


class QuizSearchResult(BaseModel):
    """ページング済み公開クイズ検索結果."""

    data: list[QuizSearchItem]
    total: int


async def search_public_quizzes(query: str, paging: Paging) -> QuizSearchResult:
    """退役・無効クイズを除外し、対象の最大スコアを優先する."""
    cypher = f"""
        MATCH (creator:User)-[:CREATE]->(quiz:Quiz)
        MATCH (quiz)-[:QUIZ_TARGET]->(target:Sentence)
        MATCH (resource:Resource {{uid: target.resource_uid}})
        WHERE creator.is_active = true
          AND NOT EXISTS {{ MATCH (quiz)-[:BROKEN_BY]->() }}
          AND (NOT quiz.quiz_type IN ['TERM2SENT', 'SENT2TERM']
               OR EXISTS {{ MATCH (:Term)-[:DEF]->(target) }})
          AND NOT EXISTS {{
              MATCH (quiz)-[:QUIZ_OPTION]->(invalid:Sentence)
              WHERE quiz.quiz_type IN ['TERM2SENT', 'SENT2TERM']
                AND NOT EXISTS {{ MATCH (:Term)-[:DEF]->(invalid) }}
          }}
        WITH creator, quiz, resource, target
        ORDER BY resource.uid, target.uid
        WITH creator, quiz, head(collect(DISTINCT resource)) AS resource,
             collect(DISTINCT target) AS targets
        WHERE $query = '' OR ANY(t IN targets WHERE toLower(t.val) CONTAINS $query)
          OR EXISTS {{
              MATCH (quiz)-[:QUIZ_TARGET|QUIZ_OPTION]->(s:Sentence)
              WHERE toLower(s.val) CONTAINS $query
                 OR EXISTS {{ MATCH (term:Term)-[:DEF]->(s)
                             WHERE toLower(term.val) CONTAINS $query }}
          }}
        UNWIND targets AS target
        {q_stats("target", OrderBy())}
        WITH creator, quiz, resource, max(stats.score) AS score
        ORDER BY score DESC, quiz.created DESC, quiz.uid ASC
        WITH collect({{
            quiz_id: quiz.uid, target_score: score,
            resource_id: resource.uid, resource_name: resource.title,
            creator_id: creator.uid, creator_username: creator.username
        }}) AS records
        {paging.return_stmt("records")}
    """
    rows, _ = await adb.cypher_query(
        cypher,
        params={"query": query.strip().lower(), **paging.params},
    )
    total, records = rows[0]
    sources = await restore_quiz_sources([r["quiz_id"] for r in records])
    quizzes = {source.quiz_id.hex: source.to_readable() for source in sources}
    return QuizSearchResult(
        total=total,
        data=[
            QuizSearchItem(
                quiz=quizzes[r["quiz_id"]],
                **{key: value for key, value in r.items() if key != "quiz_id"},
            )
            for r in records
        ],
    )
