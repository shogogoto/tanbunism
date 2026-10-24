"""敵のMP・特殊能力の生成と、行動時のMP消費."""
# ruff: noqa: PLR2004

from math import ceil, log1p
from uuid import uuid4

import pytest

from .admin import EnemyBalanceSimulationRequest, simulate_enemy_balance
from .balance import GameBalance
from .catalog import AttackSkill
from .combat import derive_enemies
from .scheduled_combat import enemy_action_skill, resolve_scheduled
from .skill_outcomes import normal_attack
from .test_scheduled_combat import answer, encounter


@pytest.mark.parametrize("region", [0, 2, 4])
def test_extra_stats_use_independent_growth_and_shared_variation(region):
    """HPと同じ式だが各能力の係数は独立する."""
    balance = GameBalance(
        enemy_mp=20,
        power_mp=2,
        region_mp_growth_percent=10,
        enemy_special_attack=15,
        power_special_attack=3,
        region_special_attack_growth_percent=20,
        enemy_special_defense=5,
        power_special_defense=1,
        region_special_defense_growth_percent=30,
    )
    factor = balance.enemy_variation("enemy")
    assert balance.enemy_extra_stats(100, region, "enemy") == {
        "mp": ceil((20 + log1p(100) * 2) * (1 + region * 0.1) * factor),
        "special_attack": ceil((15 + log1p(100) * 3) * (1 + region * 0.2) * factor),
        "special_defense": ceil((5 + log1p(100)) * (1 + region * 0.3) * factor),
    }


def test_simulation_and_live_enemies_share_extra_stats():
    """試算と実戦は同一のID・Power・領域に対し同じ能力を返す."""
    balance = GameBalance(enemy_special_defense=7, enemy_mp=25)
    trial = simulate_enemy_balance(
        EnemyBalanceSimulationRequest(
            balance=balance,
            power=100,
            achievement=3,
        ),
    )
    identity = "simulation:2:enemy:0"
    quiz_id = uuid4().hex
    enemies = derive_enemies(
        {
            "quizzes": [{"quiz_id": quiz_id}],
            "regionEnemies": {
                "2": [{"id": identity, "name": "enemy", "quizIndexes": [0]}],
            },
        },
        {quiz_id: 0},
        100,
        balance,
    )
    assert len(enemies) == 1
    for key in ("mp", "special_attack", "special_defense"):
        assert getattr(enemies[0], key) == getattr(trial.enemies[0], key)


@pytest.mark.parametrize("correct", [True, False])
def test_enemy_spends_mp_once_even_when_attack_is_prevented(correct):
    """正答で攻撃を防いでもMPを消費する。元の状態は書き換えない."""
    state, context = encounter()
    context.balance = GameBalance(base_hp=100)
    state.save.run.hp = state.save.run.maxHp = 100
    enemy = context.enemies[0]
    enemy.mp = 5
    enemy.special_attack = 40
    enemy.attack_skill = AttackSkill(
        id="fire",
        name="炎",
        damage_type="special",
        mp_cost=3,
    )
    schedule = state.save.battle.schedule
    schedule.actor_id = enemy.id
    schedule.quiz_enemy_id = enemy.id
    schedule.enemy_mp = {enemy.id: 5}
    schedule.enemy_max_mp[enemy.id] = 5
    body, results = answer(state, correct=correct)
    reply = resolve_scheduled(
        state,
        body,
        results,
        context,
        roll_enemy=lambda attack, *_: attack,
    )
    assert reply["enemy_mp"][enemy.id] == 2
    assert schedule.enemy_mp[enemy.id] == 5
    assert (
        enemy_action_skill(
            enemy,
            reply["state"].save.battle.schedule,
            normal_attack(),
        ).id
        == "normal_attack"
    )
    assert reply["damage"] == (0 if correct else 40)


def test_zero_and_large_extra_stats_are_bounded():
    """ゼロ設定はゼロのまま、巨大なPower・領域でも上限を超えない."""
    assert GameBalance(
        enemy_mp=0,
        enemy_special_attack=0,
        power_special_attack=0,
    ).enemy_extra_stats(0, 0) == {
        "mp": 0,
        "special_attack": 0,
        "special_defense": 0,
    }
    assert (
        GameBalance(
            enemy_mp=10000,
            power_mp=100,
            region_mp_growth_percent=1000,
        ).enemy_extra_stats(10**9, 100)["mp"]
        == 100000
    )
