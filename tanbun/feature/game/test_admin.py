"""管理者による領域別敵セット補修."""
# ruff: noqa: PLR2004

from asyncio import run
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from .admin import (
    EnemyBalanceSimulationRequest,
    rebuild_content_enemy_pools,
    simulate_enemy_balance,
)
from .balance import GameBalance
from .router import _region_pool_response


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
        balance.enemy_stats(100, 3, 2, variation_key=f"simulation:2:enemy:{index}")
        for index in range(3)
    ]
    assert len({(enemy.hp, enemy.attack) for enemy in result.enemies}) > 1
    assert result.min_encounter_enemies == 2
    assert result.max_encounter_enemies == 3


def test_region_growth_is_shared_by_rebuild_and_simulation() -> None:
    """領域の成長設定を本番の再構成と試算が同じ式で適用する."""
    balance = GameBalance(
        enemy_types=3,
        enemy_types_per_region=1,
        max_encounter_enemies=2,
        max_encounter_enemies_per_region=1,
    )
    result = simulate_enemy_balance(
        EnemyBalanceSimulationRequest(
            balance=balance,
            power=100,
            achievement=3,
            average_relations=3,
        ),
    )
    content = {
        "quizzes": [{"quiz_id": f"q{index}"} for index in range(15)],
        "regionQuizPools": {"2": [f"q{index}" for index in range(15)]},
        "regionEnemies": {"2": []},
    }
    rebuilt, _, _ = rebuild_content_enemy_pools(content, "simulation", balance=balance)
    assert rebuilt is not None
    enemies = rebuilt["regionEnemies"]["2"]
    assert len(enemies) == len(result.enemies) == 5
    assert [len(enemy["quizIndexes"]) for enemy in enemies] == [
        enemy.quiz_count for enemy in result.enemies
    ]
    assert [balance.enemy_stats(100, 3, 2, enemy["id"]) for enemy in enemies] == [
        (enemy.hp, enemy.attack) for enemy in result.enemies
    ]
    assert (result.min_encounter_enemies, result.max_encounter_enemies) == (1, 4)
    assert balance.encounter_range(5, 2) == (1, 4)
    assert balance.enemy_type_count(100) == 20
    assert balance.encounter_range(30, 100) == (1, 20)


def test_region_api_returns_server_roster_and_waits_for_complete_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """クライアントには共有処理で分配したIDセットを返し、未準備なら空にする."""
    module = import_module("tanbun.feature.game.router")
    balance = GameBalance(enemy_types_per_region=1)
    pool = {"ready": True, "quiz_ids": [f"q{index}" for index in range(15)]}
    monkeypatch.setattr(
        module,
        "get_or_prepare_region_pool",
        AsyncMock(return_value=pool),
    )
    monkeypatch.setattr(module, "get_game_balance", AsyncMock(return_value=balance))
    user_id, resource_id = uuid4(), uuid4()
    response = run(_region_pool_response(user_id, resource_id, 3))
    enemies = response["enemies"]
    assert len(enemies) == 5
    assert len({quiz for enemy in enemies for quiz in enemy["quiz_ids"]}) == 15
    assert enemies[0]["id"] == f"{resource_id.hex}:2:enemy:0"
    assert [len(enemy["quiz_ids"]) for enemy in enemies] == [3, 3, 3, 3, 3]
    pool["ready"] = False
    assert run(_region_pool_response(user_id, resource_id, 3))["enemies"] == []


def test_variance_is_stable_and_zero_restores_unvaried_stats() -> None:
    """同じIDは再計算で揺れず、0%では全て同じ基礎値になる."""
    balance = GameBalance(enemy_variance_percent=50)
    pairs = [balance.enemy_stats(100, 3, 0, f"enemy:{index}") for index in range(20)]
    assert pairs == [
        balance.enemy_stats(100, 3, 0, f"enemy:{index}") for index in range(20)
    ]
    assert len(set(pairs)) > 1
    uniform = GameBalance(enemy_variance_percent=0)
    assert (
        len({uniform.enemy_stats(100, 3, 0, f"enemy:{index}") for index in range(20)})
        == 1
    )


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
