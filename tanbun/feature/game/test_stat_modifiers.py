"""敵の個性・ボス補正・速さの状態効果."""
# ruff: noqa: PLR2004

from uuid import uuid4

import pytest
from pydantic import ValidationError

from .balance import GameBalance
from .catalog import (
    BuffSkill,
    DebuffSkill,
    EnemyDefinition,
    EnemyMultipliers,
    GameCatalog,
)
from .combat import derive_boss, style_enemies
from .effects import add_effect, sync_speed_effects
from .state import restore_state
from .test_scheduled_combat import encounter


def test_boss_uses_normal_base_stats_and_only_enemy_definition_multipliers():
    """ボス区分に隠れた共通倍率はなく、敵定義の倍率だけを一度掛ける。."""
    quiz = uuid4().hex
    balance = GameBalance(
        enemy_variance_percent=0,
        enemy_hp=20,
        enemy_attack=12,
        enemy_defense=4,
        enemy_mp=10,
        enemy_special_attack=8,
        enemy_special_defense=6,
        enemy_speed=10,
        power_hp=0,
        power_attack=0,
        region_hp_growth_percent=0,
        region_attack_growth_percent=0,
        region_special_attack_growth_percent=0,
    )
    boss = derive_boss(
        {
            "battle": {"bossLevel": 5, "enemies": ["boss:test"], "bossQuizIds": [quiz]},
            "quizzes": [{"quiz_id": quiz}],
        },
        {quiz: 0},
        0,
        balance,
    )
    expected = {
        "hp": 20,
        "attack": 12,
        "mp": 10,
        "defense": 4,
        "special_attack": 8,
        "special_defense": 6,
        "speed": 10,
    }
    assert {key: getattr(boss, key) for key in expected} == expected
    catalog = GameCatalog(
        enemies=[
            EnemyDefinition(
                id="boss",
                name="ボス",
                boss=True,
                multipliers=EnemyMultipliers(**dict.fromkeys(expected, 0.5)),
            ),
        ],
    )
    styled = style_enemies([boss], catalog)[0]
    assert {key: getattr(styled, key) for key in expected} == {
        key: value // 2 for key, value in expected.items()
    }
    assert boss.hp == 20
    preserved = style_enemies([styled], catalog, preserve_stats=True)[0]
    assert {key: getattr(preserved, key) for key in expected} == {
        key: getattr(styled, key) for key in expected
    }


def test_common_boss_multipliers_are_not_part_of_balance():
    """共通設定のAPIも保存データもボス倍率を持たない。."""
    assert not any(key.startswith("boss_") for key in GameBalance.model_fields)


@pytest.mark.parametrize("value", [0, -1, 401, float("inf"), float("nan")])
def test_enemy_multiplier_validation(value):
    """倍率は正の有限値だけを受け付ける."""
    with pytest.raises(ValidationError):
        EnemyMultipliers(speed=value)


def test_speed_status_changes_gauge_and_forecast_then_expires_without_stacking():
    """ゲージ進行率を維持して速さを変え、再同期・再読込で累積しない."""
    state, context = encounter()
    run, marker = state.save.run, state.save.battle
    run.mp = run.maxMp = 10
    run.speed = 10
    before = dict(marker.schedule.waits)
    for model, target, amount in [(BuffSkill, "player", 100), (DebuffSkill, "e", 50)]:
        add_effect(
            marker,
            model(
                id=target,
                name=target,
                stat="speed",
                amount=amount,
                amount_mode="percent",
                duration_turns=2,
            ),
            target,
        )
    marker.turn = 1
    sync_speed_effects(marker)
    schedule = marker.schedule
    assert schedule.speeds == {"player": 20, "e": 5, "e2": 10}
    assert schedule.waits["player"] == pytest.approx(before["player"] / 2)
    assert schedule.waits["e"] == pytest.approx(before["e"] * 2)
    first = schedule.model_dump()
    sync_speed_effects(marker)
    assert schedule.model_dump() == first
    restored = restore_state(state.model_dump_json(), context.balance)
    assert restored.save.battle.schedule.speeds == schedule.speeds
    marker.turn = 3
    sync_speed_effects(marker)
    assert schedule.speeds == {"player": 10, "e": 10, "e2": 10}
    assert schedule.waits == pytest.approx(before)


def test_speed_debuff_cannot_make_action_interval_zero_or_infinite():
    """速さをゼロ以下にしても行動不能・ゼロ除算にはしない."""
    state, _ = encounter()
    marker = state.save.battle
    add_effect(
        marker,
        DebuffSkill(id="slow", name="減速", stat="speed", amount=999),
        "player",
    )
    marker.turn = 1
    sync_speed_effects(marker)
    assert marker.schedule.speeds["player"] == 1
    assert len(marker.schedule.queue) == 10
