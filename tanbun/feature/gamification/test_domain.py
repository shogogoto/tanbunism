"""ゲーム進捗規則のテスト."""

from tanbun.feature.learning_activity.domain import LearningActivityCounts

from .domain import calculate_learning_progress, level_threshold


def test_calculate_learning_progress() -> None:
    """学習の事実を種別ごとのXPとLevelへ変換する."""
    progress = calculate_learning_progress(
        LearningActivityCounts(
            n_sentence=100,
            n_tanbun_exposure=30,
            n_quiz_created=10,
            n_quiz_answered=20,
            n_quiz_correct=15,
        ),
    )

    assert progress.xp.model_dump() == {
        "knowledge": 0,
        "tanbun_exposure": 30,
        "quiz_creation": 0,
        "quiz_answer": 100,
        "correct_bonus": 30,
    }
    assert [detail.model_dump(mode="json") for detail in progress.xp_details] == [
        {
            "source": "tanbun_exposure",
            "activity_count": 30,
            "xp_per_activity": 1,
            "earned_xp": 30,
        },
        {
            "source": "quiz_answer",
            "activity_count": 20,
            "xp_per_activity": 5,
            "earned_xp": 100,
        },
        {
            "source": "correct_bonus",
            "activity_count": 15,
            "xp_per_activity": 2,
            "earned_xp": 30,
        },
    ]
    assert progress.total_xp == 160  # noqa: PLR2004
    assert progress.level == 2  # noqa: PLR2004
    assert progress.current_level_xp == 110  # noqa: PLR2004
    assert progress.xp_for_next_level == 150  # noqa: PLR2004
    assert progress.xp_to_next_level == 40  # noqa: PLR2004


def test_importing_and_creating_quizzes_do_not_raise_review_level() -> None:
    """知識量・クイズ作成が増えても復習XPとLvは変わらない."""
    before = calculate_learning_progress(LearningActivityCounts())
    after = calculate_learning_progress(
        LearningActivityCounts(n_sentence=11433, n_quiz_created=1000),
    )
    assert after.total_xp == before.total_xp == 0
    assert after.level == before.level == 1
    assert after.xp == before.xp
    assert after.xp_details == before.xp_details


def test_level_threshold_starts_at_zero() -> None:
    """Level 1は活動前から開始する."""
    assert level_threshold(1) == 0
    assert calculate_learning_progress(LearningActivityCounts()).level == 1
