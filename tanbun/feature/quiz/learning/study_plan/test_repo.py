"""StudyPlanのrepoテスト."""

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.fixture import (
    answer_test_quiz,
    create_learning_test_resource,
    fx_learning,
    generate_test_quizzes,
    learning_resource_id,
)
from tanbun.feature.quiz.learning.study_plan.domain import StudyPlanDraft
from tanbun.feature.quiz.learning.study_plan.repo import (
    count_prepared_quizzes,
    create_study_plan,
    ensure_default_resource_study_plan,
    fetch_study_plan,
    list_prepared_quiz_counts,
)
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aregister

u = async_fixture()(fx_learning)


@mark_async_test()
async def test_prepared_counts_include_answered_dungeon_quizzes(u: LUser) -> None:
    """ゲームと共有するクイズは回答しても作成済み件数から消えない."""
    resource_id = await learning_resource_id(u.uid)
    plan = await ensure_default_resource_study_plan(u.uid, resource_id, "ダンジョン")
    requested_count = 2
    quizzes = await generate_test_quizzes(resource_id, u.uid, n_quiz=requested_count)
    assert len(quizzes) == requested_count
    assert await count_prepared_quizzes(plan.uid, u.uid) == len(quizzes)
    for index, quiz in enumerate(quizzes):
        await answer_test_quiz(quiz, u.uid, correctly=index == 0)
    assert await count_prepared_quizzes(plan.uid, u.uid) == len(quizzes)
    assert (await list_prepared_quiz_counts(u.uid))[plan.uid] == len(quizzes)


@mark_async_test()
async def test_create_and_fetch_study_plan(u: LUser):
    """設定とresourceの優先順を保存して所有者だけが取得."""
    first = await learning_resource_id(u.uid)
    second = await create_learning_test_resource(u.uid)
    draft = StudyPlanDraft(
        name="重点学習",
        resource_ids=[second, first],
        quiz_types=[QuizType.TERM2SENT, QuizType.SENT2TERM],
        n_quiz=3,
        n_option=4,
    )

    created = await create_study_plan(u.uid, draft)

    assert created.name == draft.name
    assert created.resource_ids == draft.resource_ids
    assert created.quiz_types == draft.quiz_types
    assert created.n_quiz == draft.n_quiz
    assert created.n_option == draft.n_option
    assert await fetch_study_plan(created.uid, u.uid) == created

    other = await aregister(email="study-plan-other@ex.com")
    assert await fetch_study_plan(created.uid, other.uid) is None


@mark_async_test()
async def test_default_resource_plan_uses_all_quiz_types(u: LUser) -> None:
    """自動作成Planは既存分も含めて全QuizTypeを対象にする."""
    resource_id = await learning_resource_id(u.uid)

    created = await ensure_default_resource_study_plan(
        u.uid,
        resource_id,
        "自動Plan",
    )
    restored = await ensure_default_resource_study_plan(
        u.uid,
        resource_id,
        "自動Plan",
    )

    assert created.uid == restored.uid
    assert set(restored.quiz_types) == set(QuizType)
