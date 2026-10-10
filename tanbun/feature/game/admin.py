"""管理者向けのゲームスナップショット補修."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from neomodel import adb
from pydantic import BaseModel, ValidationError

from tanbun.feature.domain.types import UUIDy, to_uuid

from .state import GameState, StateUpdate, compare_and_save, read_state


class GameDungeonAdminItem(BaseModel, frozen=True):
    """管理画面に表示するユーザー別Resourceの冒険状態."""

    user_id: UUID
    user_email: str
    resource_id: UUID
    resource_name: str
    status: str
    region_count: int
    quiz_count: int


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


def _snapshot_dungeons(state: GameState) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    if state.save.run:
        run = state.save.run
        items[run.resourceId.replace("-", "").lower()] = {
            "resource_id": run.resourceId,
            "resource_name": run.name,
            "status": run.phase,
            "content": state.save.content,
        }
    for resource_id, parked in state.save.dungeons.items():
        run = parked.run
        items[resource_id.replace("-", "").lower()] = {
            "resource_id": run.resourceId,
            "resource_name": run.name,
            "status": run.phase,
            "content": parked.content,
        }
    return items


def _snapshot_pool_counts(content: dict[str, Any] | None) -> tuple[int, int]:
    if not content:
        return 0, 0
    pools = content.get("regionQuizPools") or {}
    enemies = content.get("regionEnemies") or {}
    keys = set(pools) | set(enemies)
    quiz_ids = {
        quiz_id
        for values in pools.values()
        if isinstance(values, list)
        for quiz_id in values
        if isinstance(quiz_id, str)
    }
    if not quiz_ids:
        quiz_ids = {
            str(enemy.get("quizIndex"))
            for values in enemies.values()
            if isinstance(values, list)
            for enemy in values
            if isinstance(enemy, dict) and isinstance(enemy.get("quizIndex"), int)
        }
    return len(keys), len(quiz_ids)


async def list_game_dungeons() -> list[GameDungeonAdminItem]:
    """スナップショットとグラフの母集団を合わせて管理一覧を返す."""
    rows, _ = await adb.cypher_query(
        "MATCH (user:User) RETURN user.uid, user.email, user.game_state",
    )
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for user_id, email, raw_state in rows:
        if not raw_state:
            continue
        try:
            state = GameState.model_validate_json(raw_state)
        except ValidationError:
            continue
        for resource_key, dungeon in _snapshot_dungeons(state).items():
            region_count, quiz_count = _snapshot_pool_counts(dungeon["content"])
            result[user_id, resource_key] = {
                "user_id": user_id,
                "user_email": email,
                "resource_id": dungeon["resource_id"],
                "resource_name": dungeon["resource_name"],
                "status": dungeon["status"],
                "region_count": region_count,
                "quiz_count": quiz_count,
            }

    graph_rows, _ = await adb.cypher_query(
        """
        MATCH (user:User)-[:HAS_DUNGEON]->(dungeon:Dungeon)
            -[:BASED_ON]->(resource:Resource)
        OPTIONAL MATCH (dungeon)-[:HAS_REGION]->(region:DungeonRegion)
        OPTIONAL MATCH (region)-[:POPULATION_QUIZ]->(quiz:Quiz)
        RETURN user.uid, user.email, resource.uid, resource.title,
            count(DISTINCT region), count(DISTINCT quiz)
        """,
    )
    for user_id, email, resource_id, name, regions, quizzes in graph_rows:
        key = (user_id, resource_id)
        item = result.setdefault(
            key,
            {
                "user_id": user_id,
                "user_email": email,
                "resource_id": resource_id,
                "resource_name": name or "(名称なし)",
                "status": "母集団あり",
                "region_count": 0,
                "quiz_count": 0,
            },
        )
        item["resource_name"] = name or item["resource_name"]
        item["region_count"] = max(item["region_count"], regions)
        item["quiz_count"] = max(item["quiz_count"], quizzes)

    items = [GameDungeonAdminItem(**item) for item in result.values()]
    return sorted(items, key=lambda item: (item.user_email, item.resource_name))


async def rebuild_dungeon_enemy_pools(
    user_id: UUIDy,
    resource_id: UUIDy,
) -> dict[str, int]:
    """一つのユーザー別ダンジョンの敵表示を母集団から再構成する."""
    previous = await read_state(user_id)
    if previous.save.battle or (
        previous.save.run and previous.save.run.phase == "battle"
    ):
        raise HTTPException(409, "戦闘中は敵セットを再構築できません。")

    key = to_uuid(resource_id).hex
    updated = previous.model_copy(deep=True)
    if updated.save.run and updated.save.run.resourceId.replace("-", "").lower() == key:
        content, regions, quizzes = rebuild_content_enemy_pools(
            updated.save.content,
            key,
        )
        updated.save.content = content
    else:
        parked = next(
            (
                value
                for stored_id, value in updated.save.dungeons.items()
                if stored_id.replace("-", "").lower() == key
            ),
            None,
        )
        if parked is None:
            raise HTTPException(404, "対象ダンジョンが見つかりません。")
        content, regions, quizzes = rebuild_content_enemy_pools(
            parked.content,
            key,
        )
        parked.content = content

    if not regions:
        return {"dungeon_count": 0, "region_count": 0, "quiz_count": 0}
    updated.revision = previous.revision + 1
    rows = await compare_and_save(
        user_id,
        StateUpdate(revision=previous.revision, save=updated.save),
        updated,
    )
    if not rows:
        raise HTTPException(409, "冒険状態が更新されました。再試行してください。")
    return {"dungeon_count": 1, "region_count": regions, "quiz_count": quizzes}
