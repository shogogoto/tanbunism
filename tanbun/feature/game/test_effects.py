"""持続効果の期間・重複・精算と戦闘終了時の破棄."""
# ruff: noqa: PLR2004

from tanbun.conftest import mark_async_test

from .balance import GameBalance
from .catalog import SkillDefinition
from .combat import TurnRequest, apply_turn_result
from .effects import add_effect, modified_stat
from .gambit_slots import capacity_at_level
from .gambits import BattleAction
from .test_gambits import battle_state


def buff(**changes) -> SkillDefinition:
    """攻撃力を割合で増やす2ターンのサンプル."""
    return SkillDefinition.model_validate({
        "id": "boost",
        "name": "強化",
        "effect": "buff",
        "target": "self",
        "amount": 50,
        "amount_mode": "percent",
        "duration_turns": 2,
        **changes,
    })


def test_effect_starts_next_turn_and_expires_without_changing_base():
    """付与の次ターンから二回だけ効き、基礎値は変わらない."""
    state = battle_state()
    marker = state.save.battle
    add_effect(marker, buff(), "player")
    assert modified_stat(10, marker, "player", "attack") == 10
    marker.turn = 1
    assert modified_stat(10, marker, "player", "attack") == 15
    assert modified_stat(10, marker, "player", "defense") == 10
    marker.turn = 2
    assert modified_stat(10, marker, "player", "attack") == 15
    marker.turn = 3
    assert modified_stat(10, marker, "player", "attack") == 10
    assert state.save.run.attack == 10


def test_nonstacking_refreshes_duration_and_stacking_has_independent_expiry():
    """更新と重複を区別し、重複分はそれぞれ期限切れになる."""
    marker = battle_state().save.battle
    add_effect(marker, buff(), "player")
    marker.turn = 1
    add_effect(marker, buff(), "player")
    assert len(marker.effects) == 1
    marker.turn = 3
    assert modified_stat(10, marker, "player", "attack") == 15
    marker.effects = []
    marker.turn = 0
    skill = buff(stack_limit=2)
    add_effect(marker, skill, "player")
    marker.turn = 1
    add_effect(marker, skill, "player")
    marker.turn = 2
    assert modified_stat(10, marker, "player", "attack") == 20
    marker.turn = 3
    assert modified_stat(10, marker, "player", "attack") == 15
    add_effect(marker, skill, "player")
    add_effect(marker, skill, "player")
    assert len(marker.effects) == 2


def test_wrong_answers_do_not_apply_effect_and_combat_end_discards_it():
    """誤答では不発となり、撤退後は効果が残らない."""
    state = battle_state()
    state.save.battle.gambitAction = BattleAction(skill=buff(mp_cost=2), name="強化")
    body = TurnRequest(battle_id="battle", turn=0)
    stats = state.save.allocation.stats(GameBalance())
    wrong = apply_turn_result(state, body, {}, 0, stats)
    assert not wrong.save.battle.effects
    assert wrong.save.run.mp == 8
    correct = apply_turn_result(state, body, {"a": True}, 0, stats)
    assert len(correct.save.battle.effects) == 1
    assert correct.save.battle.turn == 1
    assert modified_stat(10, correct.save.battle, "player", "attack") == 15
    retreated = apply_turn_result(
        state,
        body.model_copy(update={"retreat": True}),
        {"a": True},
        0,
        stats,
    )
    assert retreated.save.battle is None
    assert retreated.save.run.attack == stats["attack"]


def test_debuff_targets_only_correct_enemies_and_does_not_go_below_zero():
    """未正解の敵へ付与せず、攻撃力の下限を守る."""
    state = battle_state()
    skill = buff(effect="debuff", target="all_enemies", amount_mode="fixed", amount=20)
    state.save.battle.gambitAction = BattleAction(skill=skill, name="弱体")
    result = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0),
        {"a": True, "b": False},
        0,
        state.save.allocation.stats(GameBalance()),
    )
    assert modified_stat(10, result.save.battle, "a", "attack") == 0
    assert modified_stat(10, result.save.battle, "b", "attack") == 10


def test_capacity_starts_at_one_and_only_changes_at_configured_levels():
    """一枠から始まり設定されたレベルごとに開放する."""
    balance = GameBalance()
    assert capacity_at_level(1, balance).slots == 1
    assert capacity_at_level(4, balance).slots == 1
    assert capacity_at_level(5, balance).slots == 2
    assert capacity_at_level(10, balance).slots == 3
    assert capacity_at_level(1, balance).next_level == 5
    assert capacity_at_level(100, balance).slots == 10
    assert capacity_at_level(100, balance).next_level is None
    assert capacity_at_level(3, GameBalance(gambit_levels_per_slot=3)).slots == 2


def test_percentage_recovery_uses_maximum_hp_not_current_hp():
    """HP回復の割合は最大HPを基準とし、端数は切り捨てる."""
    state = battle_state()
    skill = buff(effect="heal_hp", amount=50, mp_cost=1)
    state.save.battle.gambitAction = BattleAction(skill=skill, name="回復")
    result = apply_turn_result(
        state,
        TurnRequest(battle_id="battle", turn=0),
        {"a": True},
        0,
        state.save.allocation.stats(GameBalance()),
    )
    assert result.save.run.hp == 27
    assert result.save.run.mp == 9


@mark_async_test()
async def test_practice_incoming_damage_uses_defense_effect_until_expiry(monkeypatch):
    """模擬戦の実際の被ダメージも持続ターンと同時に元に戻る."""
    from .practice import (  # noqa: PLC0415
        PracticeTurn,
        next_practice_turn,
        practice_turn,
    )
    from .test_practice import _prepared  # noqa: PLC0415

    session = await _prepared(monkeypatch)
    session.context.balance = session.context.balance.model_copy(
        update={"damage_variance_percent": 0},
    )
    marker = session.state.save.battle
    add_effect(
        marker,
        buff(stat="defense", amount_mode="fixed", amount=100, duration_turns=1),
        "player",
    )
    marker.turn = 1
    result = practice_turn(
        PracticeTurn(session=session, turn=TurnRequest(battle_id=marker.id, turn=1)),
    )
    assert result["damage"] == len(marker.enemies)
    session.state = result["state"]
    session.state = next_practice_turn(session)
    expired = practice_turn(
        PracticeTurn(session=session, turn=TurnRequest(battle_id=marker.id, turn=2)),
    )
    assert expired["damage"] > result["damage"]
