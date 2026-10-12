"""Achievement scaling and overlapping but distinct fixed enemy sets."""
# ruff: noqa: PLR2004

from random import Random

from .balance import GameBalance
from .roster import assign_enemy_quizzes


def test_fixed_size_overlap_and_unused_population():
    """Each level has a fixed size; overlap is permitted, identical sets are not."""
    balance = GameBalance()
    groups = assign_enemy_quizzes(list(range(25)), balance, 4, rng=Random(0))  # noqa: S311
    assert len(groups) == 7
    assert all(len(group) == len(set(group)) == 5 for group in groups)
    assert len({tuple(group) for group in groups}) == 7
    assert sum(map(len, groups)) > len({i for group in groups for i in group})
    assert balance.enemy_quiz_count(4) == 5


def test_degenerate_population_terminates_without_duplicate_sets():
    """Insufficient distinct combinations reduce kinds rather than looping."""
    balance = GameBalance(quizzes_per_enemy_coefficient=5)
    assert assign_enemy_quizzes([], balance) == []
    assert assign_enemy_quizzes([0, 1], balance) == [[0, 1]]


def test_both_encounter_bounds_scale_and_allow_duplicates():
    """Available type count does not cap the number of enemy instances."""
    balance = GameBalance(encounter_growth_rate=0.5)
    assert balance.encounter_range(1, 2) == (2, 6)
    assert balance.encounter_range(0, 2) == (0, 0)
    assert balance.encounter_range(1, 100) == (20, 20)


def test_relation_count_no_longer_changes_enemy_stats():
    """Old saved relation coefficients are ignored during live calculations."""
    balance = GameBalance(relation_hp=100, relation_attack=100)
    assert balance.enemy_stats(100, 0, 2, "enemy") == balance.enemy_stats(
        100,
        1000,
        2,
        "enemy",
    )
