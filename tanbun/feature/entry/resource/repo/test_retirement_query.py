"""単文退役Cypherの単体テスト."""

from tanbun.feature.entry.resource.repo.retirement import (
    QUIZ_SENTENCE_RELS,
    retire_or_delete_sentence_qs,
)


def test_retire_or_delete_sentence_query_preserves_quiz_reference() -> None:
    """Quiz参照の有無で退役と物理削除を分ける."""
    [query] = retire_or_delete_sentence_qs(["old sentence"], {"old sentence": "n0"})

    assert "QUIZ_TARGET|QUIZ_OPTION|CORRECT" in query
    assert "MERGE (q)-[:BROKEN_BY]->(n0)" in query
    assert "REMOVE n0:Sentence" in query
    assert "SET n0:RetiredSentence" in query
    assert "DETACH DELETE n0" in query
    assert QUIZ_SENTENCE_RELS == "QUIZ_TARGET|QUIZ_OPTION|CORRECT"
