"""Damage rules shared by live combat and administrative simulations."""


def enemy_damage(attack: int, player_defense: int) -> int:
    """Deal at least one damage for unanswered or incorrect quizzes."""
    return max(1, attack - player_defense)


def player_damage(attack: int) -> int:
    """Enemies currently have no defense; correct answers deal player attack."""
    return max(0, attack)
