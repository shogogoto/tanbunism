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


def test_boss_multipliers_cover_every_stat_and_compose_with_enemy_traits():
    """ボス共通の倍率に、敵定義の個性を重ねる."""
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
        boss_hp_multiplier=2,
        boss_attack_multiplier=3,
        boss_mp_multiplier=4,
        boss_defense_multiplier=5,
        boss_special_attack_multiplier=6,
        boss_special_defense_multiplier=7,
        boss_speed_multiplier=8,
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
        "hp": 40,
        "attack": 36,
        "mp": 40,
        "defense": 20,
        "special_attack": 48,
        "special_defense": 42,
        "speed": 80,
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
    assert boss.hp == 40
    preserved = style_enemies([styled], catalog, preserve_stats=True)[0]
    assert {key: getattr(preserved, key) for key in expected} == {
        key: getattr(styled, key) for key in expected
    }


def test_old_boss_special_attack_preserves_legacy_attack_multiplier():
    """旧設定に新項目がない場合だけ、従来の攻撃倍率を引き継ぐ."""
    assert GameBalance(boss_attack_multiplier=2).boss_special_attack_multiplier == 2
    assert (
        GameBalance(
            boss_attack_multiplier=2,
            boss_special_attack_multiplier=3,
        ).boss_special_attack_multiplier
        == 3
    )


@pytest.mark.parametrize("value", [0, -1, 21, float("inf"), float("nan")])
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
