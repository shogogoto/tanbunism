"""本番の領域生成・管理者の再構成・試算で共通の固定セット抽選."""

from itertools import combinations, islice
from math import comb
from random import Random, SystemRandom

from .balance import GameBalance


def assign_enemy_quizzes(
    indices: list[int],
    balance: GameBalance,
    region: int = 0,
    *,
    rng: Random | None = None,
) -> list[list[int]]:
    """敵間の共有を許可するが、セット全体は異なる。抽選回数は有界."""
    candidates = list(dict.fromkeys(indices))
    if not candidates:
        return []
    size = min(len(candidates), balance.enemy_quiz_count(region))
    count = min(balance.enemy_type_count(region), comb(len(candidates), size))
    random = rng or SystemRandom()
    groups: set[tuple[int, ...]] = set()
    ordered: list[list[int]] = []
    for _ in range(count * 10):
        group = tuple(sorted(random.sample(candidates, size)))
        if group not in groups:
            groups.add(group)
            ordered.append(list(group))
        if len(ordered) == count:
            return ordered
    for group in islice(combinations(sorted(candidates), size), count * 2):
        if group not in groups:
            groups.add(group)
            ordered.append(list(group))
        if len(ordered) == count:
            break
    return ordered


def enemy_identity(resource_id: str, region: int, index: int) -> str:
    """敵の固定セット・個体差を識別する共通ID."""
    return f"{resource_id}:{region}:enemy:{index}"
