"""Read-only one-on-one combat estimates; no turns, XP or state are saved."""

from math import ceil

from pydantic import BaseModel, Field

from .balance import GameBalance
from .damage import damage_range


class Combatant(BaseModel, frozen=True):
    """Current HP and attack within the live game's bounds."""

    hp: int = Field(ge=1, le=100000)
    attack: int = Field(ge=0, le=100000)


class SimulatedPlayer(Combatant):
    """Only players have defense in the current game."""

    defense: int = Field(ge=0, le=100000)


class CombatSimulationRequest(BaseModel, frozen=True):
    """Two independently editable sets of combat stats."""

    player: SimulatedPlayer
    enemy: Combatant
    balance: GameBalance


class CombatSimulationResult(CombatSimulationRequest):
    """Inputs retained with the result for comparison."""

    damage_to_enemy: int
    damage_to_player: int
    damage_to_enemy_min: int
    damage_to_enemy_max: int
    damage_to_player_min: int
    damage_to_player_max: int
    correct_answers_to_defeat: int | None
    incorrect_answers_to_defeat: int


def simulate_combat(body: CombatSimulationRequest) -> CombatSimulationResult:
    """Separate correct/incorrect outcomes, not simultaneous automatic attacks."""
    outgoing_min, outgoing, outgoing_max = damage_range(
        body.player.attack,
        0,
        body.balance,
        allow_zero=True,
    )
    incoming_min, incoming, incoming_max = damage_range(
        body.enemy.attack,
        body.player.defense,
        body.balance,
    )
    return CombatSimulationResult(
        **body.model_dump(),
        damage_to_enemy=outgoing,
        damage_to_player=incoming,
        damage_to_enemy_min=outgoing_min,
        damage_to_enemy_max=outgoing_max,
        damage_to_player_min=incoming_min,
        damage_to_player_max=incoming_max,
        correct_answers_to_defeat=ceil(body.enemy.hp / outgoing) if outgoing else None,
        incorrect_answers_to_defeat=ceil(body.player.hp / incoming),
    )
