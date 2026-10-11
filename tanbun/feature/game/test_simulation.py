"""Combat estimates preserve inputs and match the live damage rules."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .balance import GameBalance
from .simulation import CombatSimulationRequest, simulate_combat


def test_combat_estimate_and_lethal_answer_counts():
    """A correct answer and an incorrect answer are independent outcomes."""
    body = CombatSimulationRequest(
        player={"hp": 35, "attack": 10, "defense": 1},
        enemy={"hp": 28, "attack": 16},
        balance=GameBalance(),
    )
    result = simulate_combat(body)
    assert result.player == body.player
    assert result.enemy == body.enemy
    assert result.damage_to_enemy == 10
    assert result.damage_to_player == 15
    assert result.damage_to_enemy_min == 8
    assert result.damage_to_enemy_max == 12
    assert result.damage_to_player_min == 12
    assert result.damage_to_player_max == 18
    assert result.correct_answers_to_defeat == 3
    assert result.incorrect_answers_to_defeat == 3


def test_zero_attack_and_high_defense():
    """Zero attack cannot defeat an enemy; high defense still takes one damage."""
    result = simulate_combat(
        CombatSimulationRequest(
            player={"hp": 10, "attack": 0, "defense": 100},
            enemy={"hp": 20, "attack": 10},
            balance=GameBalance(),
        ),
    )
    assert result.damage_to_enemy == 0
    assert result.correct_answers_to_defeat is None
    assert result.damage_to_player == 1
    assert result.incorrect_answers_to_defeat == 10


@pytest.mark.parametrize("hp", [0, -1, 100001])
def test_rejects_invalid_hp(hp):
    """Reject values outside actual game bounds."""
    with pytest.raises(ValidationError):
        CombatSimulationRequest(
            player={"hp": hp, "attack": 1, "defense": 0},
            enemy={"hp": 10, "attack": 1},
            balance=GameBalance(),
        )
