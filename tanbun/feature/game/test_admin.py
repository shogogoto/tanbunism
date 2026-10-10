"""管理者による領域別敵セット補修."""
# ruff: noqa: PLR2004

from .admin import (
    EnemyBalanceSimulationRequest,
    rebuild_content_enemy_pools,
    simulate_enemy_balance,
)
from .balance import GameBalance


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
            {
                "id": "book:0:enemy:0",
                "name": "領域 1の敵 1",
                "quizIndex": 0,
                "quizIndexes": [0],
            },
            {
                "id": "book:0:enemy:1",
                "name": "領域 1の敵 2",
                "quizIndex": 2,
                "quizIndexes": [2],
            },
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
            "id": "book:2:enemy:0",
            "name": "領域 3の敵 1",
            "quizIndex": 1,
            "quizIndexes": [1],
        },
    ]


def test_rebuild_assigns_multiple_fixed_quizzes_to_each_enemy_type() -> None:
    """3種類の敵が領域母集団を重複なく固定セットとして分担する."""
    content = {
        "quizzes": [{"quiz_id": f"quiz-{index}"} for index in range(5)],
        "regionQuizPools": {"0": [f"quiz-{index}" for index in range(5)]},
        "regionEnemies": {"0": []},
    }
    rebuilt, _, _ = rebuild_content_enemy_pools(content, "book")
    assert rebuilt is not None
    enemies = rebuilt["regionEnemies"]["0"]
    assert len(enemies) == 3
    assigned = [index for enemy in enemies for index in enemy["quizIndexes"]]
    assert sorted(assigned) == list(range(5))
    assert max(len(enemy["quizIndexes"]) for enemy in enemies) == 2


def test_enemy_simulation_uses_production_roster_and_stat_calculations() -> None:
    """試算のロスター配分と能力値は本番の計算関数と一致する."""
    balance = GameBalance(
        enemy_types=3,
        min_quizzes_per_enemy=2,
        max_quizzes_per_enemy=6,
        min_enemies=2,
        max_encounter_enemies=3,
    )
    result = simulate_enemy_balance(
        EnemyBalanceSimulationRequest(
            balance=balance,
            power=100,
            achievement=3,
            average_relations=3,
        ),
    )
    assert result.balance == balance
    assert result.pool_quiz_count == 15
    assert [enemy.quiz_count for enemy in result.enemies] == [5, 5, 5]
    assert [(enemy.hp, enemy.attack) for enemy in result.enemies] == [
        balance.enemy_stats(100, 3, 2, variation_key=f"simulation:3:{index}")
        for index in range(1, 4)
    ]
    assert len({(enemy.hp, enemy.attack) for enemy in result.enemies}) > 1
    assert result.min_encounter_enemies == 2
    assert result.max_encounter_enemies == 3


def test_reroll_selects_a_distinct_cumulative_pool_from_prepared_quizzes() -> None:
    """再選出では母集団を累積し、同じクイズを重ねない."""
    content = {
        "quizzes": [{"quiz_id": f"quiz-{index}"} for index in range(1, 16)],
        "regionQuizPools": {
            "0": [f"quiz-{index}" for index in range(1, 6)],
            "1": [f"quiz-{index}" for index in range(1, 11)],
            "2": [f"quiz-{index}" for index in range(1, 16)],
        },
        "regionEnemies": {"0": [], "1": [], "2": []},
    }

    rebuilt, regions, quiz_count = rebuild_content_enemy_pools(
        content,
        "book",
        reroll=True,
    )

    assert rebuilt is not None
    pools = rebuilt["regionQuizPools"]
    assert regions == len(content["regionQuizPools"])
    assert quiz_count == sum(len(pool) for pool in pools.values())
    assert [len(pools[str(region)]) for region in range(3)] == [
        5,
        10,
        15,
    ]
    assert len(set(pools["2"])) == len(content["quizzes"])
    assert pools["0"] == pools["1"][:5]
    assert pools["1"] == pools["2"][:10]
    assert pools["0"] != content["regionQuizPools"]["0"]
