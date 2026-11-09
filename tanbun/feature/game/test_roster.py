"""Achievement scaling and overlapping but distinct fixed enemy sets."""
# ruff: noqa: PLR2004

from random import Random
from uuid import uuid4

from .balance import GameBalance
from .enemy_stats import derive_enemies
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
    """関係数は出題元の情報として保持し、Power由来の能力値へ二重加算しない."""
    quiz_id = uuid4().hex
    content = {
        "quizzes": [{"quiz_id": quiz_id}],
        "regionEnemies": {"2": [{"id": "enemy", "name": "敵", "quizIndex": 0}]},
    }
    balance = GameBalance()
    low = derive_enemies(content, {quiz_id: 0}, 100, balance)[0]
    high = derive_enemies(content, {quiz_id: 1000}, 100, balance)[0]
    assert (low.hp, low.attack) == (high.hp, high.attack)
    assert (low.relations, high.relations) == (0, 1000)
