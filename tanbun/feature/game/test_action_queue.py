"""CTB ordering, deterministic ties and non-mutating forecasts."""
# ruff: noqa: PLR2004

from collections import Counter
from math import sqrt

import pytest

from .action_queue import ActionSchedule, interval, predict, refresh_speeds, take_next
from .balance import Allocation, GameBalance


def schedule(speeds, formula="linear", seed="battle"):
    """Initialize equal-length progress gauges."""
    return ActionSchedule(
        formula=formula,
        seed=seed,
        speeds=speeds,
        waits={key: interval(speed, formula) for key, speed in speeds.items()},
    )


def test_equal_speed_produces_equal_frequency():
    """Every tied group contains each actor exactly once."""
    queue = predict(schedule({"p": 2, "e": 2}), 12)
    for index in range(0, len(queue), 2):
        assert {item.actor_id for item in queue[index : index + 2]} == {"p", "e"}


@pytest.mark.parametrize(
    ("formula", "speeds"),
    [
        ("linear", {"p": 4, "e": 2}),
        ("sqrt", {"p": 16, "e": 4}),
    ],
)
def test_speed_ratio(formula, speeds):
    """Linear doubles at 2x speed, square root only at 4x speed."""
    queue = predict(schedule(speeds, formula), 30)
    assert Counter(item.actor_id for item in queue) == {"p": 20, "e": 10}


def test_randomness_only_affects_ties():
    """The first faster action never loses to a later slow action."""
    orders = set()
    for seed in range(30):
        queue = predict(schedule({"p": 4, "e": 2}, seed=str(seed)), 3)
        assert queue[0].actor_id == "p"
        assert [item.distance for item in queue] == [3, 6, 6]
        orders.add(tuple(item.actor_id for item in queue[1:]))
    assert orders == {("p", "e"), ("e", "p")}


def test_prediction_matches_actual_actions_without_mutation():
    """Refreshing the UI does not reroll ties or advance the real battle."""
    state = schedule({"p": 4, "e": 2, "e2": 3})
    before = state.model_copy(deep=True)
    forecast = predict(state, 30)
    assert state == before
    assert forecast == predict(state, 30)
    assert [item.actor_id for item in forecast] == [take_next(state) for _ in range(30)]


def test_speed_change_preserves_progress_and_removes_defeated_actor():
    """A half-filled gauge stays half filled after a speed buff."""
    state = schedule({"p": 2, "e": 1})
    state.waits["p"] = 3
    refresh_speeds(state, {"p": 4})
    assert state.waits == {"p": 1.5}
    assert {item.actor_id for item in predict(state)} == {"p"}


def test_sqrt_and_weight_are_continuous():
    """No agility lookup table or coarse integer rounding."""
    assert interval(2, "sqrt") == pytest.approx(12 / sqrt(2))
    assert interval(3, "sqrt") < interval(2, "sqrt")
    assert interval(4, "linear", 2) == 6


@pytest.mark.parametrize("speed", [0, -1])
def test_invalid_speed_is_rejected(speed):
    """Zero speed must not deadlock the queue."""
    with pytest.raises(ValueError, match="positive"):
        interval(speed, "linear")


def test_speed_allocation_uses_current_settings():
    """Only spent points persist; new admin values affect existing characters."""
    points = Allocation(speed=3)
    assert points.stats(GameBalance(base_speed=10, speed_per_point=2))["speed"] == 16
    assert points.stats(GameBalance(base_speed=20, speed_per_point=3))["speed"] == 29
