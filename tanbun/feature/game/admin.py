"""管理者向けのゲームスナップショット補修."""

import logging
import secrets
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks, HTTPException
from neomodel import adb
from pydantic import BaseModel, Field, ValidationError

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.notification.domain import NewNotification, NotificationKind
from tanbun.feature.notification.usecase import notify_user
from tanbun.feature.quiz.learning.study_plan.preparation_control import (
    UserPreparationLimitError,
    quiz_preparation_controller,
)
from tanbun.feature.quiz.learning.study_plan.preparation_settings import (
    get_quiz_preparation_settings,
)
from tanbun.feature.quiz.learning.study_plan.repo import (
    ensure_default_resource_study_plan,
)
from tanbun.feature.quiz.learning.study_plan.usecase import (
    prepare_additional_study_plan_quizzes,
)
from tanbun.feature.quiz.repo.restore import restore_quiz_sources

from .balance import GameBalance, get_game_balance
from .population import MAX_REGION_LEVEL, replace_region_pool
from .population import REGION_QUIZ_COUNT as QUIZZES_PER_REGION_LEVEL
from .roster import assign_enemy_quizzes as _assign_enemy_quizzes
from .roster import enemy_identity
from .state import GameState, StateUpdate, compare_and_save, read_state

_scheduled_pool_rebuilds: set[tuple[str, str]] = set()
logger = logging.getLogger(__name__)


class GameDungeonAdminItem(BaseModel, frozen=True):
    """管理画面に表示するユーザー別Resourceの冒険状態."""

    user_id: UUID
    user_email: str
    resource_id: UUID
    resource_name: str
    status: str
    region_count: int
    quiz_count: int
    regions: list["GameDungeonRegionPool"] = Field(default_factory=list)


class GameDungeonRegionQuiz(BaseModel, frozen=True):
    """管理画面で確認する母集団クイズの要約."""

    quiz_id: str
    quiz_type: str
    statement: str


class GameDungeonRegionPool(BaseModel, frozen=True):
    """一つの領域に固定されたクイズ母集団."""

    level: int
    quizzes: list[GameDungeonRegionQuiz]


class EnemyBalanceSimulationRequest(BaseModel, frozen=True):
    """ゲームの実ロジックで敵構成を試算する入力."""

    balance: GameBalance
    power: int = Field(ge=0, le=1_000_000_000)
    achievement: int = Field(ge=1, le=MAX_REGION_LEVEL)
    average_relations: int = Field(ge=0, le=1_000_000)


class SimulatedEnemy(BaseModel, frozen=True):
    """敵ロスター編成関数と能力計算関数から導出した1種の敵."""

    index: int
    quiz_count: int
    hp: int
    attack: int
    relations: int


class EnemyBalanceSimulationResult(BaseModel, frozen=True):
    """設定候補とダンジョン条件に対する試算結果."""

    power: int
    achievement: int
    pool_quiz_count: int
    average_relations: int
    balance: GameBalance
    min_encounter_enemies: int
    max_encounter_enemies: int
    enemies: list[SimulatedEnemy]


def simulate_enemy_balance(
    request: EnemyBalanceSimulationRequest,
) -> EnemyBalanceSimulationResult:
    """実際の敵クイズ分配・能力計算を使い、指定条件の敵を試算する."""
    pool_quiz_count = request.achievement * QUIZZES_PER_REGION_LEVEL
    quiz_groups = _assign_enemy_quizzes(
        list(range(pool_quiz_count)),
        request.balance,
        request.achievement - 1,
    )
    enemies = []
    for index, quiz_indexes in enumerate(quiz_groups):
        hp, attack = request.balance.enemy_stats(
            request.power,
            request.average_relations,
            request.achievement - 1,
            variation_key=enemy_identity("simulation", request.achievement - 1, index),
        )
        enemies.append(
            SimulatedEnemy(
                index=index + 1,
                quiz_count=len(quiz_indexes),
                hp=hp,
                attack=attack,
                relations=min(
                    request.balance.relation_cap,
                    request.average_relations,
                ),
            ),
        )
    encounter_lower, encounter_upper = request.balance.encounter_range(
        len(enemies),
        request.achievement - 1,
    )
    return EnemyBalanceSimulationResult(
        power=request.power,
        achievement=request.achievement,
        pool_quiz_count=pool_quiz_count,
        average_relations=request.average_relations,
        balance=request.balance,
        min_encounter_enemies=encounter_lower,
        max_encounter_enemies=encounter_upper,
        enemies=enemies,
    )


def _legacy_pool_ids(
    enemies: list[dict[str, Any]],
    quizzes: list[dict[str, Any]],
) -> list[str]:
    """旧敵配列から領域で固定されていたクイズIDを取り出す."""
    indices = [
        index
        for enemy in enemies
        for index in enemy.get("quizIndexes", [enemy.get("quizIndex")])
        if isinstance(index, int) and 0 <= index < len(quizzes)
    ]
    return list(
        dict.fromkeys(
            quizzes[index]["quiz_id"]
            for index in indices
            if quizzes[index].get("quiz_id")
        ),
    )


def _rebuild_region(
    region_key: str,
    *,
    resource_id: str,
    quizzes: list[dict[str, Any]],
    quiz_by_id: dict[str, tuple[int, dict[str, Any]]],
    pool_ids: list[str] | None,
    previous_enemies: list[dict[str, Any]],
    balance: GameBalance,
) -> tuple[list[str], list[dict[str, Any]]]:
    """母集団を重複なく固定クイズセットに分けた敵ロスターを作る."""
    region = int(region_key)
    old_by_id = {str(enemy.get("id")): enemy for enemy in previous_enemies}
    source_ids = (
        pool_ids
        if pool_ids is not None
        else _legacy_pool_ids(
            previous_enemies,
            quizzes,
        )
    )
    stored_ids: list[str] = []
    indices: list[int] = []
    seen: set[str] = set()
    for quiz_id in source_ids:
        key = quiz_id.replace("-", "").lower()
        record = quiz_by_id.get(key)
        if key in seen or not record:
            continue
        seen.add(key)
        index, quiz = record
        stored_id = quiz["quiz_id"]
        stored_ids.append(stored_id)
        indices.append(index)
    groups = _assign_enemy_quizzes(indices, balance, region)
    if not groups:
        return stored_ids, []
    enemies = []
    for enemy_index, group in enumerate(groups):
        enemy_id = enemy_identity(resource_id, region, enemy_index)
        old = old_by_id.get(enemy_id, {})
        enemies.append({
            "id": enemy_id,
            "name": old.get("name", f"領域 {region + 1}の敵 {enemy_index + 1}"),
            "quizIndex": group[0],
            "quizIndexes": group,
        })
    return stored_ids, enemies


def _rerolled_region_pools(
    quizzes: list[dict[str, Any]],
    old_pools: dict[str, list[str]],
    region_keys: list[str],
) -> dict[str, list[str]]:
    """準備済みクイズを重複なくシャッフルし、領域ごとの累積セットを作る."""
    candidates = list(
        dict.fromkeys(str(quiz["quiz_id"]) for quiz in quizzes if quiz.get("quiz_id")),
    )
    required = (max(map(int, region_keys)) + 1) * QUIZZES_PER_REGION_LEVEL
    if len(candidates) < required:
        raise HTTPException(
            409,
            f"母集団の再選出には準備済みクイズが{required}問必要です。現在は{len(candidates)}問です。",
        )
    secrets.SystemRandom().shuffle(candidates)
    previous_ids = [
        quiz_id
        for region_key in sorted(region_keys, key=int)
        for quiz_id in old_pools.get(region_key, [])
        if isinstance(quiz_id, str)
    ]
    previous_unique = list(dict.fromkeys(previous_ids))
    if (
        len(candidates) > len(previous_unique)
        and candidates[: len(previous_unique)] == previous_unique
    ):
        candidates = candidates[1:] + candidates[:1]
    first_region = min(region_keys, key=int)
    old_first_pool = old_pools.get(first_region, [])
    if len(candidates) > len(old_first_pool) and set(
        candidates[: len(old_first_pool)],
    ) == set(old_first_pool):
        candidates = candidates[1:] + candidates[:1]
    return {
        region_key: candidates[: (int(region_key) + 1) * QUIZZES_PER_REGION_LEVEL]
        for region_key in sorted(region_keys, key=int)
    }


def rebuild_content_enemy_pools(
    content: dict[str, Any] | None,
    resource_id: str,
    *,
    reroll: bool = False,
    balance: GameBalance | None = None,
) -> tuple[dict[str, Any] | None, int, int]:
    """保存済み母集団または再選出した母集団から敵配列を組み立てる."""
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

    replacement_pools = (
        _rerolled_region_pools(quizzes, old_pools, region_keys) if reroll else {}
    )
    balance = balance or GameBalance()

    rebuilt_regions = {
        str(region_key): _rebuild_region(
            str(region_key),
            resource_id=resource_id,
            quizzes=quizzes,
            quiz_by_id=quiz_by_id,
            pool_ids=(
                replacement_pools[region_key] if reroll else old_pools.get(region_key)
            ),
            previous_enemies=old_enemies.get(region_key, []),
            balance=balance,
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
    balance = await get_game_balance()
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
            balance=balance,
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
            balance=balance,
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
            str(index)
            for values in enemies.values()
            if isinstance(values, list)
            for enemy in values
            if isinstance(enemy, dict)
            for index in enemy.get("quizIndexes", [enemy.get("quizIndex")])
            if isinstance(index, int)
        }
    return len(keys), len(quiz_ids)


def _snapshot_region_pools(
    content: dict[str, Any] | None,
) -> list[GameDungeonRegionPool]:
    if not content:
        return []
    quizzes = content.get("quizzes") or []
    quizzes_by_id = {
        str(quiz.get("quiz_id", "")).replace("-", "").lower(): quiz
        for quiz in quizzes
        if quiz.get("quiz_id")
    }
    pools = content.get("regionQuizPools") or {}
    if not pools:
        pools = {
            region: _legacy_pool_ids(enemies, quizzes)
            for region, enemies in (content.get("regionEnemies") or {}).items()
        }
    result = []
    for region_key, quiz_ids in sorted(pools.items(), key=lambda item: int(item[0])):
        region_quizzes = []
        for quiz_id in quiz_ids:
            quiz = quizzes_by_id.get(str(quiz_id).replace("-", "").lower())
            if quiz:
                region_quizzes.append(
                    GameDungeonRegionQuiz(
                        quiz_id=str(quiz_id),
                        quiz_type=str(quiz.get("quiz_type", "")),
                        statement=str(quiz.get("statement", "")),
                    ),
                )
        result.append(
            GameDungeonRegionPool(
                level=int(region_key) + 1,
                quizzes=region_quizzes,
            ),
        )
    return result


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
                "regions": _snapshot_region_pools(dungeon["content"]),
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
                "regions": [],
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
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, int | bool]:
    """一つのダンジョンの母集団を再選出し、敵とグラフを同期する."""
    previous = await read_state(user_id)
    if previous.save.battle or (
        previous.save.run and previous.save.run.phase == "battle"
    ):
        raise HTTPException(409, "戦闘中は敵セットを再構築できません。")

    key = to_uuid(resource_id).hex
    source_content = _dungeon_content(previous, key)
    required = _required_pool_quiz_count(source_content)
    available = len(
        {
            str(quiz.get("quiz_id", "")).replace("-", "").lower()
            for quiz in (source_content or {}).get("quizzes", [])
            if quiz.get("quiz_id")
        },
    )
    if required and available < required:
        if background_tasks is None:
            raise HTTPException(
                409,
                "クイズを準備しています。完了後に再度お試しください。",
            )
        await _schedule_pool_rebuild(
            user_id,
            resource_id,
            required - available,
            background_tasks,
        )
        return {
            "dungeon_count": 0,
            "region_count": 0,
            "quiz_count": 0,
            "preparing": True,
        }

    return await _rebuild_dungeon_enemy_pools_now(user_id, resource_id)


def _dungeon_content(state: GameState, resource_key: str) -> dict[str, Any] | None:
    if (
        state.save.run
        and state.save.run.resourceId.replace("-", "").lower() == resource_key
    ):
        return state.save.content
    parked = next(
        (
            dungeon
            for resource_id, dungeon in state.save.dungeons.items()
            if resource_id.replace("-", "").lower() == resource_key
        ),
        None,
    )
    return parked.content if parked else None


def _required_pool_quiz_count(content: dict[str, Any] | None) -> int:
    if not content:
        return 0
    keys = list(
        dict.fromkeys([
            *(content.get("regionQuizPools") or {}),
            *(content.get("regionEnemies") or {}),
        ]),
    )
    return (max(map(int, keys)) + 1) * QUIZZES_PER_REGION_LEVEL if keys else 0


async def _prepare_and_rebuild_pool(
    user_id: UUIDy,
    resource_id: UUIDy,
    additional_count: int,
) -> None:
    key = to_uuid(resource_id).hex
    try:
        state = await read_state(user_id)
        content = _dungeon_content(state, key)
        if content is None:
            return
        rows, _ = await adb.cypher_query(
            "MATCH (r:Resource {uid: $uid}) RETURN r.title",
            {"uid": key},
        )
        plan = await ensure_default_resource_study_plan(
            user_id,
            resource_id,
            (rows[0][0] if rows else None) or "ダンジョン",
        )
        preparation = await prepare_additional_study_plan_quizzes(
            plan.uid,
            user_id,
            additional_count,
            send_notification=False,
        )
        await _sync_study_plan_quizzes(user_id, resource_id, plan.uid, content)
        await _rebuild_dungeon_enemy_pools_now(user_id, resource_id)
        await notify_user(
            user_id,
            NewNotification(
                kind=NotificationKind.QUIZ_PREPARATION_COMPLETE,
                title="ダンジョンのクイズ準備完了",
                description="クイズを追加し、母集団と敵セットを再選出しました。",
                href="/admin",
            ),
        )
        logger.info(
            "Prepared %d quizzes and rebuilt dungeon pools for user %s resource %s",
            preparation.added_count,
            user_id,
            resource_id,
        )
    except Exception as error:
        logger.exception(
            "Queued dungeon quiz preparation/rebuild failed for %s",
            resource_id,
        )
        try:
            await notify_user(
                user_id,
                NewNotification(
                    kind=NotificationKind.QUIZ_PREPARATION_FAILED,
                    title="ダンジョンのクイズ準備に失敗しました",
                    description=(
                        str(error).strip() or "時間をおいて再度お試しください。"
                    )[:500],
                    href="/admin",
                ),
            )
        except Exception:
            logger.exception("Failed to notify about dungeon quiz preparation failure")
    finally:
        _scheduled_pool_rebuilds.discard((to_uuid(user_id).hex, key))


async def _sync_study_plan_quizzes(
    user_id: UUIDy,
    resource_id: UUIDy,
    plan_id: UUIDy,
    content: dict[str, Any],
) -> None:
    key = to_uuid(resource_id).hex
    required = _required_pool_quiz_count(content)
    quiz_rows, _ = await adb.cypher_query(
        """
            MATCH (plan:StudyPlan {uid: $plan_id})-[:STUDY]->(resource:Resource)
            MATCH (user:User {uid: $user_id})-[:LEARN]->(quiz:Quiz)
                -[:QUIZ_TARGET]->(sentence:Sentence)
            WHERE sentence.resource_uid = resource.uid
              AND quiz.quiz_type IN plan.quiz_types
              AND NOT EXISTS { MATCH (quiz)-[:BROKEN_BY]->() }
            RETURN DISTINCT quiz.uid
            ORDER BY quiz.uid
            LIMIT $limit
            """,
        {
            "plan_id": to_uuid(plan_id).hex,
            "user_id": to_uuid(user_id).hex,
            "limit": required,
        },
    )
    sources = await restore_quiz_sources([row[0] for row in quiz_rows])
    readable = [source.to_readable().model_dump(mode="json") for source in sources]
    updated = await read_state(user_id)
    latest_content = _dungeon_content(updated, key)
    if latest_content is None:
        return
    by_id = {
        str(quiz.get("quiz_id", "")).replace("-", "").lower(): quiz
        for quiz in [*(latest_content.get("quizzes") or []), *readable]
        if quiz.get("quiz_id")
    }
    _replace_dungeon_content(
        updated,
        key,
        {**latest_content, "quizzes": list(by_id.values())},
    )
    await _save_admin_state(user_id, updated)


async def _schedule_pool_rebuild(
    user_id: UUIDy,
    resource_id: UUIDy,
    additional_count: int,
    background_tasks: BackgroundTasks,
) -> None:
    key = (to_uuid(user_id).hex, to_uuid(resource_id).hex)
    if key in _scheduled_pool_rebuilds:
        return
    settings = await get_quiz_preparation_settings()
    try:
        await quiz_preparation_controller.reserve(
            user_id,
            max_jobs_per_user=settings.max_concurrent_jobs_per_user,
        )
    except UserPreparationLimitError as error:
        raise HTTPException(409, str(error)) from error
    _scheduled_pool_rebuilds.add(key)
    background_tasks.add_task(
        quiz_preparation_controller.execute,
        user_id,
        max_concurrent_jobs=settings.max_concurrent_jobs,
        operation=_prepare_and_rebuild_pool,
        args=(user_id, resource_id, additional_count),
    )


def _replace_dungeon_content(
    state: GameState,
    key: str,
    content: dict[str, Any],
) -> None:
    if state.save.run and state.save.run.resourceId.replace("-", "").lower() == key:
        state.save.content = content
        return
    for resource_id, dungeon in state.save.dungeons.items():
        if resource_id.replace("-", "").lower() == key:
            dungeon.content = content
            return


async def _save_admin_state(user_id: UUIDy, state: GameState) -> None:
    expected_revision = state.revision
    state.revision = expected_revision + 1
    rows = await compare_and_save(
        user_id,
        StateUpdate(revision=expected_revision, save=state.save),
        state,
    )
    if not rows:
        raise HTTPException(409, "冒険状態が更新されました。再試行してください。")


async def _rebuild_dungeon_enemy_pools_now(
    user_id: UUIDy,
    resource_id: UUIDy,
) -> dict[str, int]:
    previous = await read_state(user_id)
    balance = await get_game_balance()
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
            reroll=True,
            balance=balance,
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
            reroll=True,
            balance=balance,
        )
        parked.content = content

    if not regions:
        return {"dungeon_count": 0, "region_count": 0, "quiz_count": 0}
    for region_key, quiz_ids in (content.get("regionQuizPools") or {}).items():
        await replace_region_pool(user_id, resource_id, int(region_key) + 1, quiz_ids)
    updated.revision = previous.revision + 1
    rows = await compare_and_save(
        user_id,
        StateUpdate(revision=previous.revision, save=updated.save),
        updated,
    )
    if not rows:
        raise HTTPException(409, "冒険状態が更新されました。再試行してください。")
    return {"dungeon_count": 1, "region_count": regions, "quiz_count": quizzes}
