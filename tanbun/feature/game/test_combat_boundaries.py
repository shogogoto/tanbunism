"""戦闘計算とDB・通知・時計の境界を守る."""

import subprocess  # noqa: S404 - 新しいPythonで読み込み時の依存を検証する
import sys
from copy import deepcopy
from unittest.mock import Mock
from uuid import UUID

import pytest
from neomodel import adb

from . import (
    balance,
    balance_models,
    combat,
    combat_models,
    combat_resolution,
    enemy_stats,
    scheduled_combat,
    scheduled_resolution,
)
from .catalog import default_catalog
from .practice import PracticeSession, scheduled_practice_turn
from .rewards import GoldSettings
from .test_scheduled_combat import answer, encounter


@pytest.mark.parametrize(
    "module_name",
    [
        "balance_models",
        "combat_models",
        "enemy_stats",
        "combat_resolution",
        "scheduled_resolution",
    ],
)
def test_calculation_imports_do_not_load_database_or_live_orchestration(module_name):
    """テスト環境の既存importに隠れた依存を、新しいプロセスで検出する."""
    module = f"tanbun.feature.game.{module_name}"
    code = (
        "import importlib, sys; "
        f"importlib.import_module({module!r}); "
        "forbidden = ['neomodel', 'tanbun.feature.game.balance', "
        "'tanbun.feature.game.combat', 'tanbun.feature.game.scheduled_combat', "
        "'tanbun.feature.game.state', 'tanbun.feature.game.catalog_repo']; "
        "assert not set(forbidden) & sys.modules.keys()"
    )
    result = subprocess.run(  # noqa: S603 - モジュール名は上の固定リストのみ
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("public_module", "defining_module"),
    [
        (balance, balance_models),
        (combat, combat_models),
        (combat, enemy_stats),
        (combat, combat_resolution),
        (scheduled_combat, scheduled_resolution),
    ],
)
def test_existing_imports_reexport_the_same_types_and_functions(
    public_module,
    defining_module,
):
    """クラスを複製せず、既存の型判定・呼び出し先・モデル名を維持する."""
    definitions = {
        key: value
        for key, value in vars(defining_module).items()
        if not key.startswith("_")
        and getattr(value, "__module__", None) == defining_module.__name__
    }
    assert definitions
    for key, value in definitions.items():
        assert getattr(public_module, key) is value


@pytest.mark.parametrize("actor", ["player", "e"])
@pytest.mark.parametrize("correct", [True, False])
def test_resolution_needs_no_io_and_preserves_all_input_snapshots(
    actor,
    correct,
    monkeypatch,
):
    """ダメージの乱数を固定すれば同じ結果になり、入力状態や設定を変更しない."""
    state, context = encounter()
    state.save.battle.schedule.actor_id = actor
    context.gold = GoldSettings(
        base=10,
        power_coefficient=0,
        achievement_growth_percent=0,
        variance_percent=0,
    )
    body, results = answer(state, correct=correct)
    before = (
        state.model_dump_json(),
        context.model_dump_json(),
        body.model_dump_json(),
        dict(results),
    )

    def reject_io(*_args, **_kwargs):
        pytest.fail("戦闘結果の計算中にDBへアクセスした")

    monkeypatch.setattr(adb, "cypher_query", reject_io)
    first = scheduled_resolution.resolve_scheduled(
        state,
        body,
        results,
        context,
        roll_player=lambda *_: 1000,
        roll_enemy=lambda *_: 7,
    )
    second = scheduled_resolution.resolve_scheduled(
        state,
        body,
        results,
        context,
        roll_player=lambda *_: 1000,
        roll_enemy=lambda *_: 7,
    )
    assert first == second
    assert before == (
        state.model_dump_json(),
        context.model_dump_json(),
        body.model_dump_json(),
        results,
    )
    if correct:
        assert first["state"].save.economy.gold > state.save.economy.gold
    else:
        assert first["state"].save.run.hp < state.save.run.hp


def test_practice_calls_the_live_calculation_engine_without_persisting(monkeypatch):
    """模擬戦は乱数の渡し方だけを変え、実戦と同じ計算関数へ委譲する."""
    state, context = encounter()
    state.save.run.answerDeadline = None
    body, results = answer(state)
    session = PracticeSession(state=state, context=context, seed=1, server_now=0)
    engine = Mock(wraps=scheduled_resolution.resolve_scheduled)

    def reject_io(*_args, **_kwargs):
        pytest.fail("模擬戦の精算中にDBへアクセスした")

    monkeypatch.setattr(adb, "cypher_query", reject_io)
    monkeypatch.setattr(scheduled_resolution, "resolve_scheduled", engine)
    reply = scheduled_practice_turn(session, body, results)
    engine.assert_called_once()
    assert engine.call_args.args == (state, body, results, context)
    assert reply["state"] is not state


@pytest.mark.parametrize("representation", ["uuid", "hex", "hyphenated"])
def test_enemy_generation_uses_only_supplied_data_and_uuid_values(representation):
    """UUID正規化をDBの型に依存させず、通常敵・複製敵・ボスを同じ値で導く."""
    identity = UUID("12345678-1234-5678-1234-567812345678")
    quiz_id = {"uuid": identity, "hex": identity.hex, "hyphenated": str(identity)}[
        representation
    ]
    content = {
        "quizzes": [{"quiz_id": quiz_id}],
        "regionEnemies": {"0": [{"id": "e", "name": "敵", "quizIndex": 0}]},
        "battle": {
            "enemies": ["e#2"],
            "quizIndices": {"e#2": 0},
        },
    }
    before = deepcopy(content)
    settings = balance_models.GameBalance()
    catalog = default_catalog()
    catalog_before = catalog.model_dump_json()
    generated = enemy_stats.derive_enemies(content, {identity.hex: 2}, 100, settings)
    assert [enemy.id for enemy in generated] == ["e", "e#2"]
    originals = [enemy.model_dump() for enemy in generated]
    assert enemy_stats.style_enemies(generated, catalog) == enemy_stats.style_enemies(
        generated,
        catalog,
    )
    assert [enemy.model_dump() for enemy in generated] == originals
    assert content == before
    assert catalog.model_dump_json() == catalog_before
    boss_content = {
        **content,
        "battle": {
            "enemies": ["boss:5"],
            "bossLevel": 5,
            "bossQuizIds": [identity.hex],
        },
    }
    boss = enemy_stats.derive_boss(boss_content, {identity.hex: 2}, 100, settings)
    assert boss is not None
    assert boss.id == "boss:5"
    assert boss.quizIndexes == [0]
