"""Resource更新時の単文退役テスト."""

from uuid import uuid4

import pytest
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.entry.resource.repo.delete import delete_resource
from tanbun.feature.entry.resource.repo.diff_update.repo import update_resource_diff
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.parsing.tree2net import parse2net
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.management.errors import QuizNotFoundError
from tanbun.feature.quiz.management.repo import list_broken_created_quiz_references
from tanbun.feature.quiz.management.usecase import delete_quiz, repair_quiz_reference
from tanbun.feature.user.label import LUser


@async_fixture()
async def u() -> LUser:  # noqa: D103
    return await LUser(email="retirement@gmail.com").save()


@mark_async_test()
async def test_removed_quiz_sentence_is_retired_but_unreferenced_is_deleted(
    u: LUser,
) -> None:
    """Quizの参照は保ち、参照されない削除単文はDBから消す."""
    old = """
        # title
            remain
            referenced old sentence
            disposable old sentence
    """
    _, resource = await save_text(u.uid, old)
    quiz_uid = uuid4().hex
    rows, _ = await adb.cypher_query(
        """
        MATCH (sentence:Sentence {
            resource_uid: $resource_uid,
            val: 'referenced old sentence'
        })
        CREATE (quiz:Quiz {uid: $quiz_uid})-[:QUIZ_TARGET]->(sentence)
        RETURN sentence.uid
        """,
        params={"resource_uid": resource.uid.hex, "quiz_uid": quiz_uid},
    )
    sentence_uid = rows[0][0]

    updated = parse2net("# title\n  remain\n")
    await update_resource_diff(resource.uid, updated)

    retired, _ = await adb.cypher_query(
        """
        MATCH (quiz:Quiz {uid: $quiz_uid})-[:QUIZ_TARGET]->(
            sentence:RetiredSentence {uid: $sentence_uid}
        )
        MATCH (quiz)-[:BROKEN_BY]->(sentence)
        RETURN sentence.val, sentence.retired_at IS NOT NULL
        """,
        params={"quiz_uid": quiz_uid, "sentence_uid": sentence_uid},
    )
    disposable, _ = await adb.cypher_query(
        "MATCH (n {val: 'disposable old sentence'}) RETURN count(n)",
    )

    assert retired == [["referenced old sentence", True]]
    assert disposable == [[0]]


@mark_async_test()
async def test_resource_delete_keeps_only_quiz_referenced_sentence(u: LUser) -> None:
    """Resource自体の削除でもQuizの復旧材料だけを残す."""
    text = """
        # delete target
            referenced sentence
            disposable sentence
    """
    _, resource = await save_text(u.uid, text)
    quiz_uid = uuid4().hex
    await adb.cypher_query(
        """
        MATCH (sentence:Sentence {
            resource_uid: $resource_uid,
            val: 'referenced sentence'
        })
        CREATE (quiz:Quiz {uid: $quiz_uid})-[:QUIZ_OPTION]->(sentence)
        """,
        params={"resource_uid": resource.uid.hex, "quiz_uid": quiz_uid},
    )

    await delete_resource(resource.uid)

    counts, _ = await adb.cypher_query(
        """
        OPTIONAL MATCH (resource:Resource {uid: $resource_uid})
        OPTIONAL MATCH (active:Sentence {resource_uid: $resource_uid})
        OPTIONAL MATCH (quiz:Quiz {uid: $quiz_uid})-[:BROKEN_BY]->(
            retired:RetiredSentence {resource_uid: $resource_uid}
        )
        RETURN
            count(DISTINCT resource),
            count(DISTINCT active),
            count(DISTINCT retired)
        """,
        params={"resource_uid": resource.uid.hex, "quiz_uid": quiz_uid},
    )

    assert counts == [[0, 0, 1]]


@mark_async_test()
async def test_reattach_retired_sentence_moves_quiz_edges(u: LUser) -> None:
    """競合解決で選んだ現行単文へQuiz関係を付け替える."""
    text = """
        # reattach target
            retired source
            replacement destination
    """
    _, resource = await save_text(u.uid, text)
    quiz_uid = uuid4().hex
    rows, _ = await adb.cypher_query(
        """
        MATCH (source:Sentence {
            resource_uid: $resource_uid,
            val: 'retired source'
        })
        MATCH (replacement:Sentence {
            resource_uid: $resource_uid,
            val: 'replacement destination'
        })
        MATCH (user:User {uid: $user_uid})
        CREATE (user)-[:CREATE]->(quiz:Quiz {
            uid: $quiz_uid,
            quiz_type: $quiz_type
        })
        CREATE (quiz)-[:QUIZ_TARGET]->(source)
        RETURN source.uid, replacement.uid
        """,
        params={
            "resource_uid": resource.uid.hex,
            "quiz_uid": quiz_uid,
            "quiz_type": QuizType.TERM2SENT.name,
            "user_uid": to_uuid(u.uid).hex,
        },
    )
    retired_uid, replacement_uid = rows[0]
    updated = parse2net("# reattach target\n  replacement destination\n")
    await update_resource_diff(resource.uid, updated)

    [broken] = await list_broken_created_quiz_references(u.uid)
    assert broken.quiz_id.hex == quiz_uid
    assert broken.quiz_type is QuizType.TERM2SENT
    assert broken.retired_sentence_id.hex == retired_uid
    assert broken.roles == ["QUIZ_TARGET"]

    other = await LUser(email="retirement-other@gmail.com").save()
    with pytest.raises(QuizNotFoundError):
        await repair_quiz_reference(
            quiz_uid,
            retired_uid,
            replacement_uid,
            other.uid,
        )

    result = await repair_quiz_reference(
        quiz_uid,
        retired_uid,
        replacement_uid,
        u.uid,
    )
    state, _ = await adb.cypher_query(
        """
        MATCH (:Quiz {uid: $quiz_uid})-[:QUIZ_TARGET]->(
            replacement:Sentence {uid: $replacement_uid}
        )
        OPTIONAL MATCH (retired:RetiredSentence {uid: $retired_uid})
        OPTIONAL MATCH (:Quiz {uid: $quiz_uid})-[:BROKEN_BY]->(broken)
        RETURN count(DISTINCT replacement), count(DISTINCT retired),
            count(DISTINCT broken)
        """,
        params={
            "quiz_uid": quiz_uid,
            "retired_uid": retired_uid,
            "replacement_uid": replacement_uid,
        },
    )

    assert result.quiz_targets == 1
    assert result.retained is False
    assert state == [[1, 0, 0]]


@mark_async_test()
async def test_deleting_broken_quiz_purges_orphaned_retired_sentence(
    u: LUser,
) -> None:
    """Quiz削除後に参照がなくなった退役単文を残さない."""
    _, resource = await save_text(
        u.uid,
        "# orphan cleanup\n  will retire\n  remain\n",
    )
    quiz_uid = uuid4().hex
    rows, _ = await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_uid})
        MATCH (sentence:Sentence {
            resource_uid: $resource_uid,
            val: 'will retire'
        })
        CREATE (user)-[:CREATE]->(quiz:Quiz {uid: $quiz_uid})
        CREATE (quiz)-[:QUIZ_TARGET]->(sentence)
        RETURN sentence.uid
        """,
        params={
            "user_uid": to_uuid(u.uid).hex,
            "resource_uid": resource.uid.hex,
            "quiz_uid": quiz_uid,
        },
    )
    retired_uid = rows[0][0]
    await update_resource_diff(
        resource.uid,
        parse2net("# orphan cleanup\n  remain\n"),
    )

    await delete_quiz(quiz_uid, u.uid)

    rows, _ = await adb.cypher_query(
        "MATCH (retired:RetiredSentence {uid: $uid}) RETURN count(retired)",
        params={"uid": retired_uid},
    )
    assert rows == [[0]]
