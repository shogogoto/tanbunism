"""Damage rules shared by live combat and administrative simulations."""

from math import floor
from secrets import randbelow

from .balance import GameBalance


def damage_range(
    attack: int,
    defense: int,
    balance: GameBalance,
    *,
    allow_zero: bool = False,
) -> tuple[int, int, int]:
    """Return minimum, nominal, and maximum damage for the configured formula."""
    raw = (
        attack * balance.damage_attack_multiplier
        - defense / balance.damage_defense_divisor
    )
    nominal = max(0 if allow_zero and attack == 0 else 1, floor(raw))
    if nominal == 0:
        return 0, 0, 0
    spread = balance.damage_variance_percent / 100
    minimum = max(1, floor(nominal * (1 - spread)))
    maximum = max(1, floor(nominal * (1 + spread)))
    return minimum, nominal, maximum


def _damage(attack: int, defense: int, balance: GameBalance) -> int:
    minimum, _, maximum = damage_range(attack, defense, balance)
    if maximum == 0:
        return 0
    return minimum + randbelow(maximum - minimum + 1)


def enemy_damage(attack: int, player_defense: int, balance: GameBalance) -> int:
    """Damage dealt to the player for unanswered or incorrect quizzes."""
    return _damage(attack, player_defense, balance)


def player_damage(attack: int, balance: GameBalance) -> int:
    """Enemies have no defense; correct answers deal configured damage."""
    minimum, _, maximum = damage_range(attack, 0, balance, allow_zero=True)
    return minimum + randbelow(maximum - minimum + 1) if maximum else 0
