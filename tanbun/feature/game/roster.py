"""本番の領域生成・管理者の再構成・試算で共通の敵クイズ分配."""

from .balance import GameBalance


def assign_enemy_quizzes(
    indices: list[int],
    balance: GameBalance,
    region: int = 0,
) -> list[list[int]]:
    """重複なく固定セットへ分配する。空の母集団には敵を作らない."""
    if not indices:
        return []
    count = min(
        balance.enemy_type_count(region),
        max(1, len(indices) // balance.min_quizzes_per_enemy),
    )
    indices = indices[: count * balance.max_quizzes_per_enemy]
    minimum = min(balance.min_quizzes_per_enemy, len(indices) // count)
    groups: list[list[int]] = [[] for _ in range(count)]
    cursor = 0
    for group in groups:
        for _ in range(minimum):
            group.append(indices[cursor])
            cursor += 1
    while cursor < len(indices):
        eligible = [
            group for group in groups if len(group) < balance.max_quizzes_per_enemy
        ]
        if not eligible:
            break
        min(eligible, key=len).append(indices[cursor])
        cursor += 1
    return groups


def enemy_identity(resource_id: str, region: int, index: int) -> str:
    """敵の固定セット・個体差を識別する共通ID."""
    return f"{resource_id}:{region}:enemy:{index}"
