"""クイズ生成repoのテスト."""

import pytest
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.parsing.sysnet.sysnode import DUMMY_SENTENCE
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.domain import QuizSource
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.errors import InsufficientOptionsError
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser

from .repo import check_duplicate_for_precreate, generate_quiz, prepare_quiz_gen

u = async_fixture()(fx_u)

TERM_OPTION_COUNT = 5
RELATION_OPTION_COUNT = 3


async def _generate_term_quiz(
    quiz_type: QuizType,
    user_id: UUIDy,
) -> QuizSource:
    """用語を持つ単文からクイズを生成."""
    target = await LSentence.nodes.first(val="ccc")
    return await generate_quiz(
        quiz_type,
        CandidateType.ALL,
        target.uid,
        TERM_OPTION_COUNT,
        user_id,
    )


async def _generate_relation_quiz(
    quiz_type: QuizType,
    user_id: UUIDy,
) -> QuizSource:
    """対象と関係先を指定して関係クイズを生成."""
    target = await LSentence.nodes.first(val="ccc")
    pair = await LSentence.nodes.first(val="parent")
    return await generate_quiz(
        quiz_type,
        CandidateType.ALL,
        target.uid,
        RELATION_OPTION_COUNT,
        user_id,
        correct_sent_uids=[pair.uid],
    )


@pytest.mark.parametrize(
    "quiz_type",
    [QuizType.REL2PAIR, QuizType.PAIR2REL],
)
@mark_async_test()
async def test_prepare_relation_quiz(quiz_type: QuizType, u: LUser):
    """関係クイズ用の正解と誤答肢を準備する."""
    target = await LSentence.nodes.first(val="ccc")
    pair = await LSentence.nodes.first(val="parent")

    for _ in range(3):
        distractor_ids, correct_ids = await prepare_quiz_gen(
            quiz_type,
            CandidateType.ALL,
            target.uid,
            RELATION_OPTION_COUNT,
            [pair.uid],
        )
        assert len(distractor_ids) == RELATION_OPTION_COUNT - 1
        assert correct_ids == [pair.uid]
        assert pair.uid not in distractor_ids


@pytest.mark.parametrize(
    "quiz_type",
    [QuizType.TERM2SENT, QuizType.SENT2TERM],
)
@mark_async_test()
async def test_generate_term_quiz(quiz_type: QuizType, u: LUser):
    """用語系クイズの選択肢と正誤判定を生成する."""
    source = await _generate_term_quiz(quiz_type, u.uid)
    quiz = source.to_readable()

    assert len(quiz.options) == TERM_OPTION_COUNT
    assert quiz.is_correct([source.get_id_by_sent("ccc")])
    assert not quiz.is_correct([source.get_id_by_sent("ccc1")])


@pytest.mark.parametrize(
    "quiz_type",
    [QuizType.REL2PAIR, QuizType.PAIR2REL],
)
@mark_async_test()
async def test_generate_relation_quiz(quiz_type: QuizType, u: LUser):
    """関係系クイズの選択肢と正誤判定を生成する."""
    source = await _generate_relation_quiz(quiz_type, u.uid)
    quiz = source.to_readable()
    correct_id = source.get_id_by_sent("parent")

    assert len(quiz.options) == RELATION_OPTION_COUNT
    assert quiz.is_correct([correct_id])
    for distractor_id in set(quiz.options) - {correct_id}:
        assert not quiz.is_correct([distractor_id])
    if quiz_type is QuizType.PAIR2REL:
        assert len(set(quiz.options.values())) == len(quiz.options)


@mark_async_test()
async def test_generated_quiz_has_creator_and_learner(u: LUser):
    """自分用に生成したクイズは作成者と学習者の両方に紐づく."""
    source = await _generate_term_quiz(QuizType.TERM2SENT, u.uid)
    rows, _ = await adb.cypher_query(
        """
        MATCH (user: User {uid: $user_id}), (quiz: Quiz {uid: $quiz_id})
        RETURN
            EXISTS { (user)-[:CREATE]->(quiz) },
            EXISTS { (user)-[:LEARN]->(quiz) }
        """,
        params={
            "user_id": to_uuid(u.uid).hex,
            "quiz_id": to_uuid(source.quiz_id).hex,
        },
    )
    assert rows == [[True, True]]


@pytest.mark.parametrize("quiz_type", list(QuizType))
@mark_async_test()
async def test_generate_quiz_without_correct_option(
    quiz_type: QuizType,
    u: LUser,
):
    """クイズの正解の選択肢がなくて何も選ばないのが正解."""
    target = await LSentence.nodes.first(val="ccc")
    pair = await LSentence.nodes.first(val="parent")
    source = await generate_quiz(
        quiz_type,
        CandidateType.ALL,
        target.uid,
        RELATION_OPTION_COUNT,
        u.uid,
        no_correct_option=True,
        correct_sent_uids=[pair.uid] if not quiz_type.has_term else None,
    )
    quiz = source.to_readable()

    assert len(quiz.options) == RELATION_OPTION_COUNT
    assert quiz.is_correct([])


@mark_async_test()
async def test_check_duplication(u: LUser):
    """クイズ作成の重複チェック."""
    source = await _generate_term_quiz(QuizType.TERM2SENT, u.uid)
    assert await check_duplicate_for_precreate(
        source.target_id,
        source.quiz_type,
        list(source.sources),
        source.correct_ids,
    )
    assert not await check_duplicate_for_precreate(
        source.target_id,
        QuizType.REL2PAIR,
        list(source.sources),
        source.correct_ids,
    )


@mark_async_test()
async def test_undefined_term_is_not_a_quiz_target(u: LUser):
    """説明待ち用語のダミー文からはクイズを生成しない."""
    target = await LSentence.nodes.first(val="ccc")
    dummy = await LSentence(
        val=DUMMY_SENTENCE,
        resource_uid=target.resource_uid,
    ).save()

    with pytest.raises(InsufficientOptionsError, match="説明のない用語"):
        await generate_quiz(
            QuizType.TERM2SENT,
            CandidateType.ALL,
            dummy.uid,
            3,
            u.uid,
        )
