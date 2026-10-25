"""Pure CTB scheduling. Virtual distances only; never wall-clock timers."""

from hashlib import blake2b
from math import isclose, sqrt
from typing import Annotated, Literal

from pydantic import BaseModel, Field

SpeedFormula = Literal["linear", "sqrt"]
ResourceScale = Annotated[float, Field(ge=0.00001, le=100000, allow_inf_nan=False)]


class QueueEntry(BaseModel):
    """One forecast position in virtual, not elapsed, time."""

    actor_id: str
    distance: float


class ActionSchedule(BaseModel):
    """Encounter-local rules and sealed current action; HP is server-owned."""

    formula: SpeedFormula = "sqrt"
    seed: str
    step: int = 0
    waits: dict[str, float] = Field(default_factory=dict)
    speeds: dict[str, int] = Field(default_factory=dict)
    base_speeds: dict[str, int] = Field(default_factory=dict)
    enemy_hp: dict[str, int] = Field(default_factory=dict)
    enemy_mp: dict[str, int] = Field(default_factory=dict)
    enemy_max_hp: dict[str, int] = Field(default_factory=dict)
    enemy_max_mp: dict[str, int] = Field(default_factory=dict)
    enemy_hp_scale: dict[str, ResourceScale] = Field(default_factory=dict)
    enemy_mp_scale: dict[str, ResourceScale] = Field(default_factory=dict)
    actor_id: str | None = None
    quiz_enemy_id: str | None = None
    queue: list[QueueEntry] = Field(default_factory=list)


def interval(speed: int, formula: SpeedFormula, weight: float = 1) -> float:
    """Calculate an interval using an arbitrary common distance scale."""
    if speed <= 0 or weight <= 0:
        msg = "Speed and action weight must be positive"
        raise ValueError(msg)
    return 12 * weight / (sqrt(speed) if formula == "sqrt" else speed)


def take_next(schedule: ActionSchedule) -> str | None:
    """Jump directly to the next event; randomize only genuinely equal ties."""
    if not schedule.waits:
        return None
    delta = min(schedule.waits.values())
    schedule.waits = {
        key: max(0, value - delta) for key, value in schedule.waits.items()
    }
    tied = [
        key for key, value in schedule.waits.items() if isclose(value, 0, abs_tol=1e-9)
    ]
    actor = min(
        tied,
        key=lambda key: blake2b(
            f"{schedule.seed}:{schedule.step}:{key}".encode(),
            digest_size=16,
        ).digest(),
    )
    schedule.step += 1
    schedule.waits[actor] = interval(schedule.speeds[actor], schedule.formula)
    return actor


def predict(schedule: ActionSchedule, count: int = 10) -> list[QueueEntry]:
    """Clone scheduling state, including tie entropy. Include current action."""
    copy = schedule.model_copy(deep=True)
    entries = [QueueEntry(actor_id=copy.actor_id, distance=0)] if copy.actor_id else []
    distance = 0.0
    while len(entries) < count and copy.waits:
        distance += min(copy.waits.values())
        actor = take_next(copy)
        entries.append(QueueEntry(actor_id=actor, distance=distance))
    return entries[:count]


def refresh_speeds(schedule: ActionSchedule, speeds: dict[str, int]) -> None:
    """Keep gauge progress when speed changes; discard defeated actors."""
    for actor in list(schedule.waits):
        if actor not in speeds:
            del schedule.waits[actor]
        else:
            old = interval(schedule.speeds[actor], schedule.formula)
            schedule.waits[actor] *= interval(speeds[actor], schedule.formula) / old
    schedule.speeds = speeds
