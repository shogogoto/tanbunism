"""ゲーム進捗規則のテスト."""

import pytest

from tanbun.feature.learning_activity.domain import LearningActivityCounts

from .domain import calculate_learning_progress, level_from_xp, level_threshold


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
    assert progress.level == 6  # noqa: PLR2004
    assert progress.current_level_xp == 10  # noqa: PLR2004
    assert progress.xp_for_next_level == 60  # noqa: PLR2004
    assert progress.xp_to_next_level == 50  # noqa: PLR2004


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


@pytest.mark.parametrize("coefficient", [1, 3, 10, 20, 10000])
@pytest.mark.parametrize("level", [1, 2, 3, 10, 100, 10**9])
def test_linear_level_cost_boundaries(coefficient: int, level: int) -> None:
    """任意の係数で境界を正確に逆算し、レベル毎の必要量はLv * 係数となる."""
    threshold = level_threshold(level, coefficient)
    assert level_from_xp(threshold, coefficient) == level
    if level > 1:
        assert level_from_xp(threshold - 1, coefficient) == level - 1
    assert level_threshold(level + 1, coefficient) - threshold == level * coefficient
