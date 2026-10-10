"""管理者向けのゲームスナップショット補修."""

from typing import Any

from fastapi import HTTPException

from tanbun.feature.domain.types import UUIDy

from .state import StateUpdate, compare_and_save, read_state


def _legacy_pool_ids(
    enemies: list[dict[str, Any]],
    quizzes: list[dict[str, Any]],
) -> list[str]:
    """旧敵配列から領域で固定されていたクイズIDを取り出す."""
    return [
        quizzes[enemy["quizIndex"]]["quiz_id"]
        for enemy in enemies
        if isinstance(enemy.get("quizIndex"), int)
        and 0 <= enemy["quizIndex"] < len(quizzes)
        and quizzes[enemy["quizIndex"]].get("quiz_id")
    ]


def _rebuild_region(
    region_key: str,
    *,
    resource_id: str,
    quizzes: list[dict[str, Any]],
    quiz_by_id: dict[str, tuple[int, dict[str, Any]]],
    pool_ids: list[str] | None,
    previous_enemies: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """領域の母集団と、それに対応する安定した敵IDを組み立てる."""
    region = int(region_key)
    enemy_by_quiz = {
        quizzes[index]["quiz_id"].replace("-", "").lower(): enemy
        for enemy in previous_enemies
        if isinstance((index := enemy.get("quizIndex")), int)
        and 0 <= index < len(quizzes)
        and quizzes[index].get("quiz_id")
    }
    source_ids = (
        pool_ids
        if pool_ids is not None
        else _legacy_pool_ids(
            previous_enemies,
            quizzes,
        )
    )
    stored_ids: list[str] = []
    enemies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for quiz_id in source_ids:
        key = quiz_id.replace("-", "").lower()
        record = quiz_by_id.get(key)
        if key in seen or not record:
            continue
        seen.add(key)
        index, quiz = record
        stored_id = quiz["quiz_id"]
        old_enemy = enemy_by_quiz.get(key, {})
        stored_ids.append(stored_id)
        enemies.append(
            {
                "id": old_enemy.get("id", f"{resource_id}:{region}:{stored_id}"),
                "name": old_enemy.get(
                    "name",
                    f"領域 {region + 1}の敵 {len(enemies) + 1}",
                ),
                "quizIndex": index,
            },
        )
    return stored_ids, enemies


def rebuild_content_enemy_pools(
    content: dict[str, Any] | None,
    resource_id: str,
) -> tuple[dict[str, Any] | None, int, int]:
    """既存の各領域のクイズ母集団を明示し、敵配列をそこから再構成する."""
    if not content:
        return content, 0, 0

    quizzes = content.get("quizzes") or []
    quiz_by_id = {
        str(quiz.get("quiz_id", "")).replace("-", "").lower(): (index, quiz)
        for index, quiz in enumerate(quizzes)
        if quiz.get("quiz_id")
    }
    old_enemies = content.get("regionEnemies") or {}
    old_pools = content.get("regionQuizPools") or {}
    region_keys = list(dict.fromkeys([*old_pools, *old_enemies]))
    if not region_keys:
        return content, 0, 0

    rebuilt_regions = {
        str(region_key): _rebuild_region(
            str(region_key),
            resource_id=resource_id,
            quizzes=quizzes,
            quiz_by_id=quiz_by_id,
            pool_ids=old_pools.get(region_key),
            previous_enemies=old_enemies.get(region_key, []),
        )
        for region_key in region_keys
    }
    new_pools = {region: result[0] for region, result in rebuilt_regions.items()}
    new_enemies = {region: result[1] for region, result in rebuilt_regions.items()}
    population_size = sum(len(pool) for pool in new_pools.values())

    rebuilt = {
        **content,
        "regionQuizPools": new_pools,
        "regionEnemies": new_enemies,
    }
    return rebuilt, len(region_keys), population_size


async def rebuild_user_enemy_pools(user_id: UUIDy) -> dict[str, int]:
    """戦闘中でないユーザーの保存済みダンジョン敵セットを再構成する."""
    previous = await read_state(user_id)
    if previous.save.battle or (
        previous.save.run and previous.save.run.phase == "battle"
    ):
        raise HTTPException(409, "戦闘中は敵セットを再構築できません。")

    updated = previous.model_copy(deep=True)
    regions = population_quizzes = dungeons = 0
    if updated.save.run:
        resource_id = updated.save.run.resourceId
        content, count, population = rebuild_content_enemy_pools(
            updated.save.content,
            resource_id,
        )
        if count:
            updated.save.content = content
            regions += count
            population_quizzes += population
            dungeons += 1

    for resource_id, parked in updated.save.dungeons.items():
        content, count, population = rebuild_content_enemy_pools(
            parked.content,
            resource_id,
        )
        if count:
            parked.content = content
            regions += count
            population_quizzes += population
            dungeons += 1

    if not dungeons:
        return {"dungeon_count": 0, "region_count": 0, "quiz_count": 0}

    updated.revision = previous.revision + 1
    rows = await compare_and_save(
        user_id,
        StateUpdate(revision=previous.revision, save=updated.save),
        updated,
    )
    if not rows:
        raise HTTPException(409, "冒険状態が更新されました。再試行してください。")
    return {
        "dungeon_count": dungeons,
        "region_count": regions,
        "quiz_count": population_quizzes,
    }
