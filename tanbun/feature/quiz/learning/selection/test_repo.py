"""学習用クイズの対象選択repoのテスト."""

from random import Random

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.parsing.sysnet.sysnode import DUMMY_SENTENCE
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.quiz.learning.fixture import (
    fx_learning,
    learning_resource_id,
)
from tanbun.feature.quiz.learning.selection.domain import (
    QuizTargetOrder,
    QuizTargetPool,
)
from tanbun.feature.quiz.learning.selection.repo import (
    fetch_covered_sent_ids,
    fetch_sort_by_score,
    fetch_target_ids,
    fetch_uncovered_sent_ids,
)
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.tanbun.repo.detail import fetch_tanbuns_with_detail
from tanbun.feature.user.label import LUser

u = async_fixture()(fx_learning)


@mark_async_test()
async def test_fetch_uncovered(u: LUser):
    """クイズ生成後の単文を未coverage対象から除外."""
    rid = await learning_resource_id(u.uid)
    target = await LSentence.nodes.first(val="a")
    dummy = await LSentence(val=DUMMY_SENTENCE, resource_uid=rid.hex).save()
    assert dummy.uid not in await fetch_uncovered_sent_ids(
        rid,
        u.uid,
        QuizType.REL2PAIR,
    )
    assert target.uid in await fetch_uncovered_sent_ids(
        rid,
        u.uid,
        QuizType.TERM2SENT,
    )

    await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )

    assert target.uid not in await fetch_uncovered_sent_ids(
        rid,
        u.uid,
        QuizType.TERM2SENT,
    )


@mark_async_test()
async def test_fetch_covered(u: LUser):
    """クイズ生成後の単文をcoverage対象として取得."""
    rid = await learning_resource_id(u.uid)
    target = await LSentence.nodes.first(val="a")
    assert await fetch_covered_sent_ids(rid, u.uid, QuizType.TERM2SENT) == []

    await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )

    assert await fetch_covered_sent_ids(
        rid,
        u.uid,
        QuizType.TERM2SENT,
    ) == [target.uid]


@mark_async_test()
async def test_sort_by_score(u: LUser):
    """スコア順で並べる."""
    rid = await learning_resource_id(u.uid)
    ids = await fetch_uncovered_sent_ids(rid, u.uid, QuizType.REL2PAIR)
    sorted_ids = await fetch_sort_by_score(ids)
    knowdes = await fetch_tanbuns_with_detail(sorted_ids, None)
    sentences = [tanbun.sentence for tanbun in knowdes.values()]
    assert sentences[:2] == ["s", "pre_s"]


@mark_async_test()
async def test_fetch_target_ids(u: LUser):
    """poolからスコア順で指定件数の対象を取得."""
    rid = await learning_resource_id(u.uid)
    highest = await LSentence.nodes.first(val="s")

    targets = await fetch_target_ids(
        rid,
        u.uid,
        QuizType.TERM2SENT,
        QuizTargetPool.UNCOVERED,
        QuizTargetOrder.HIGH_SCORE,
        limit=1,
    )
    assert targets == [highest.uid]

    low_score_targets = await fetch_target_ids(
        rid,
        u.uid,
        QuizType.TERM2SENT,
        QuizTargetPool.UNCOVERED,
        QuizTargetOrder.LOW_SCORE,
        limit=1,
    )
    assert low_score_targets != targets

    await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        highest.uid,
        3,
        u.uid,
    )
    assert await fetch_target_ids(
        rid,
        u.uid,
        QuizType.TERM2SENT,
        QuizTargetPool.COVERED,
        QuizTargetOrder.HIGH_SCORE,
        limit=1,
    ) == [highest.uid]


@mark_async_test()
async def test_fetch_random_target_ids(u: LUser):
    """同じseedでは同じ対象をランダム選択."""
    rid = await learning_resource_id(u.uid)
    pool = await fetch_uncovered_sent_ids(rid, u.uid, QuizType.TERM2SENT)
    limit = 2

    async def fetch(seed: int):
        return await fetch_target_ids(
            rid,
            u.uid,
            QuizType.TERM2SENT,
            QuizTargetPool.UNCOVERED,
            QuizTargetOrder.RANDOM,
            limit=limit,
            rng=Random(seed),  # noqa: S311
        )

    targets = await fetch(0)
    assert len(targets) == limit
    assert set(targets) <= set(pool)
    assert targets == await fetch(0)
