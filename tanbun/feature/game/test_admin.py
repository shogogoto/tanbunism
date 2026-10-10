"""管理者による領域別敵セット補修."""

from .admin import rebuild_content_enemy_pools


def test_rebuild_migrates_legacy_enemy_assignments_without_widening_pool() -> None:
    """移行時も既存領域の母集団を勝手に広げない."""
    content = {
        "quizzes": [
            {"quiz_id": "quiz-1"},
            {"quiz_id": "quiz-2"},
            {"quiz_id": "quiz-3"},
        ],
        "regionEnemies": {
            "0": [
                {"id": "stable-1", "name": "敵A", "quizIndex": 0},
                {"id": "stable-2", "name": "敵B", "quizIndex": 2},
            ],
        },
    }

    rebuilt, regions, quiz_count = rebuild_content_enemy_pools(content, "book")

    assert regions == 1
    assert quiz_count == len(["quiz-1", "quiz-3"])
    assert rebuilt is not None
    assert rebuilt["regionQuizPools"] == {"0": ["quiz-1", "quiz-3"]}
    assert rebuilt["regionEnemies"] == {
        "0": [
            {"id": "stable-1", "name": "敵A", "quizIndex": 0},
            {"id": "stable-2", "name": "敵B", "quizIndex": 2},
        ],
    }


def test_rebuild_uses_explicit_population_and_drops_missing_quizzes() -> None:
    """明示母集団だけを使い、削除済み・重複クイズは除外する."""
    content = {
        "quizzes": [{"quiz_id": "quiz-1"}, {"quiz_id": "quiz-2"}],
        "regionQuizPools": {"2": ["quiz-2", "missing", "quiz-2"]},
        "regionEnemies": {"2": []},
    }

    rebuilt, regions, quiz_count = rebuild_content_enemy_pools(content, "book")

    assert regions == 1
    assert quiz_count == 1
    assert rebuilt is not None
    assert rebuilt["regionQuizPools"] == {"2": ["quiz-2"]}
    assert rebuilt["regionEnemies"]["2"] == [
        {
            "id": "book:2:quiz-2",
            "name": "領域 3の敵 1",
            "quizIndex": 1,
        },
    ]
