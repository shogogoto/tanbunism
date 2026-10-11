"""Read-only one-on-one combat estimates; no turns, XP or state are saved."""

from math import ceil

from pydantic import BaseModel, Field

from .damage import enemy_damage, player_damage


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


class CombatSimulationResult(CombatSimulationRequest):
    """Inputs retained with the result for comparison."""

    damage_to_enemy: int
    damage_to_player: int
    correct_answers_to_defeat: int | None
    incorrect_answers_to_defeat: int


def simulate_combat(body: CombatSimulationRequest) -> CombatSimulationResult:
    """Separate correct/incorrect outcomes, not simultaneous automatic attacks."""
    outgoing = player_damage(body.player.attack)
    incoming = enemy_damage(body.enemy.attack, body.player.defense)
    return CombatSimulationResult(
        **body.model_dump(),
        damage_to_enemy=outgoing,
        damage_to_player=incoming,
        correct_answers_to_defeat=ceil(body.enemy.hp / outgoing) if outgoing else None,
        incorrect_answers_to_defeat=ceil(body.player.hp / incoming),
    )
