"""端末間で共有する試作版の冒険スナップショット."""
# Frontend snapshot field names are intentionally retained for legacy migration.
# ruff: noqa: N815

from time import time
from typing import Literal

from fastapi import HTTPException
from neo4j.exceptions import TransientError
from neomodel import adb
from pydantic import BaseModel, Field, model_validator

from tanbun.feature.domain.types import UUIDy, to_uuid

from .access import SLOT_SECONDS, get_adventure_access
from .balance import Allocation
from .settings import get_battle_settings

MAX_SNAPSHOT_BYTES = 1000000
MAX_VISITED_DUNGEONS = 1000


class Run(BaseModel):
    """保存時に検査する攻略中の状態."""

    resourceId: str = Field(max_length=64)
    name: str = Field(max_length=1000)
    hp: int = Field(ge=0, le=100000)
    maxHp: int = Field(ge=1, le=100000)
    attack: int = Field(ge=0, le=100000)
    defense: int = Field(ge=0, le=100000)
    moves: int = Field(ge=0, le=5)
    kills: int = Field(ge=0, le=10000)
    enemyHp: int = Field(ge=0, le=100000)
    enemyMaxHp: int = Field(ge=1, le=100000)
    quizCursor: int = Field(ge=0)
    readIds: list[str] = Field(
        max_length=100,
        description="見たよで進んだ単文IDを選択順に保存。最後が現在地。休憩でも順序を維持。",
    )
    phase: Literal["path", "battle", "rest", "defeated", "cleared"]
    answerDeadline: int | None = Field(default=None, ge=0)
    answerSeconds: int | None = Field(default=None, ge=1, le=1500)
    enemyId: str | None = Field(default=None, max_length=200)


class BattleMarker(BaseModel):
    """途中のHP/選択は保存せず、未精算の戦闘と撤退先だけを保存する."""

    id: str
    turn: int = Field(default=0, ge=0)
    region: int = Field(ge=0)
    checkpoint: str = Field(default="@entrance", max_length=64)
    enemies: list[str] = Field(min_length=1, max_length=20)


class GameSave(BaseModel):
    """攻略数と固定された出題セット."""

    version: Literal[2] = 2
    clears: dict[str, int] = Field(default_factory=dict, max_length=1000)
    visitedDungeons: list[str] = Field(
        default_factory=list,
        max_length=MAX_VISITED_DUNGEONS,
    )
    run: Run | None = None
    content: dict | None = None
    battleFeedback: str | None = Field(default=None, max_length=1000)
    allocation: Allocation = Field(default_factory=Allocation)
    battle: BattleMarker | None = None
    maps: dict[str, "DungeonMap"] = Field(default_factory=dict, max_length=1000)
    dungeons: dict[str, "ParkedDungeon"] = Field(default_factory=dict, max_length=1000)

    @model_validator(mode="after")
    def bounded_snapshot(self) -> "GameSave":
        """保存量とHP・攻略数の最低限の整合性を検査する."""
        if len(self.model_dump_json().encode()) > MAX_SNAPSHOT_BYTES:
            msg = "冒険状態が大きすぎます。"
            raise ValueError(msg)
        if any(value < 0 for value in self.clears.values()):
            msg = "攻略数は0以上です。"
            raise ValueError(msg)
        if self.run and self.run.hp > self.run.maxHp:
            msg = "HPが最大値を超えています。"
            raise ValueError(msg)
        return self


class MapPlace(BaseModel):
    """初めて開拓した達成度帯を保持する地点."""

    id: str = Field(min_length=1, max_length=64)
    region: int = Field(ge=0)


class MapEdge(BaseModel):
    """知識の関係とゲーム内の寄り道を区別する道."""

    from_: str = Field(alias="from", min_length=1, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    kind: Literal["relation", "detour"]


class DungeonMap(BaseModel):
    """移動履歴とは別に、開拓した場所と現在地を保存する."""

    current: str = Field(default="@entrance", max_length=64)
    places: list[MapPlace] = Field(default_factory=list, max_length=5000)
    edges: list[MapEdge] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def valid_locations(self) -> "DungeonMap":
        """存在しない地点・重複地点を保存しない."""
        ids = {place.id for place in self.places}
        if len(ids) != len(self.places) or "@entrance" in ids:
            msg = "開拓地点が重複しています。"
            raise ValueError(msg)
        ids.add("@entrance")
        if self.current not in ids or any(
            edge.from_ not in ids or edge.to not in ids or edge.from_ == edge.to
            for edge in self.edges
        ):
            msg = "マップに存在しない地点です。"
            raise ValueError(msg)
        return self


class ParkedDungeon(BaseModel):
    """ダンジョン切替でもHP・現在地・固定クイズを失わない."""

    run: Run
    content: dict | None = None

    @model_validator(mode="after")
    def valid_pause(self) -> "ParkedDungeon":
        """戦闘からの切替と不正なHPを保存しない."""
        if self.run.phase == "battle" or self.run.hp > self.run.maxHp:
            msg = "戦闘中または不正なHPのダンジョンは中断できません。"
            raise ValueError(msg)
        return self


class GameState(BaseModel):
    """競合検知用の更新番号付きの冒険."""

    revision: int = 0
    save: GameSave = Field(default_factory=GameSave)


class StateUpdate(BaseModel):
    """保存と冒険権消費を一つのトランザクションにする入力."""

    revision: int = Field(ge=0)
    save: GameSave
    consume_access: bool = False


async def read_state(user_id: UUIDy) -> GameState:
    """本人の冒険を読み出す。初期状態では書き込みしない."""
    rows, _ = await adb.cypher_query(
        "MATCH (u:User {uid: $uid}) RETURN u.game_state",
        {"uid": to_uuid(user_id).hex},
    )
    if not rows:
        raise HTTPException(404, "ユーザーが見つかりません。")
    state = GameState.model_validate_json(rows[0][0]) if rows[0][0] else GameState()
    state.save.visitedDungeons = known_dungeons(state.save)
    return state


async def recover_state(user_id: UUIDy) -> GameState:
    """時計枠が回復したら残歩数に関わらず補充。HP・戦闘・現在地は維持する."""
    state = await read_state(user_id)
    run = state.save.run
    if not run or run.phase == "defeated" or not run.hp:
        return state
    if not (await get_adventure_access(user_id)).available:
        return state
    next_state = state.model_copy(deep=True)
    runs = [
        next_state.save.run,
        *(item.run for item in next_state.save.dungeons.values()),
    ]
    for current in runs:
        if current and current.phase != "defeated" and current.hp:
            current.moves = 0
            if current.phase == "rest":
                current.phase = "path"
    next_state.revision += 1
    update = StateUpdate(
        revision=state.revision,
        save=next_state.save,
        consume_access=True,
    )
    # Reuse the lock/revision/clock-slot CAS, including recovery from two devices.
    try:
        rows = await compare_and_save(user_id, update, next_state)
    except TransientError as cause:
        if cause.code != "Neo.TransientError.Transaction.DeadlockDetected":
            raise
        return await read_state(user_id)
    return next_state if rows else await read_state(user_id)


def known_dungeons(save: GameSave) -> list[str]:
    """旧保存の攻略済み・攻略中も補完する。存在しない過去の履歴は作らない."""
    return list(
        dict.fromkeys([
            *save.visitedDungeons,
            *save.dungeons,
            *save.maps,
            *([save.run.resourceId] if save.run else []),
            *save.clears,
        ]),
    )[:MAX_VISITED_DUNGEONS]


async def write_state(user_id: UUIDy, update: StateUpdate) -> GameState:
    """別端末の更新を上書きせず、保存と入場権を同時に確定する."""
    previous = await read_state(user_id)
    validate_snapshot_battle(previous, update)
    # Allocation has its own budget validation and must not be overwritten by snapshots.
    update.save.allocation = previous.save.allocation
    run = update.save.run
    old = previous.save.run
    merge_exploration(previous.save, update.save)
    history = list(
        dict.fromkeys([
            *previous.save.visitedDungeons,
            *update.save.clears,
        ]),
    )[:MAX_VISITED_DUNGEONS]
    if run and (not old or old.resourceId != run.resourceId):
        history = list(dict.fromkeys([run.resourceId, *history]))[:MAX_VISITED_DUNGEONS]
    # The server retains history even if a retreat or an older client omits it.
    update.save.visitedDungeons = history
    if run and run.phase == "battle" and not update.save.battleFeedback:
        if (
            old
            and (old.phase, old.resourceId, old.quizCursor, old.readIds)
            == (run.phase, run.resourceId, run.quizCursor, run.readIds)
            and old.answerDeadline is not None
        ):
            run.answerDeadline = old.answerDeadline
            run.answerSeconds = old.answerSeconds
        else:
            quizzes = (update.save.content or {}).get("quizzes", [])
            enemies = (update.save.content or {}).get("regionEnemies", {})
            enemy = next(
                (
                    enemy
                    for pool in enemies.values()
                    for enemy in pool
                    if enemy.get("id") == run.enemyId
                ),
                None,
            )
            quiz_index = (
                enemy.get("quizIndex", run.quizCursor) if enemy else run.quizCursor
            )
            kind = (
                quizzes[quiz_index % len(quizzes)].get("quiz_type") if quizzes else None
            )
            if kind not in {"sent2term", "term2sent", "pair2rel", "rel2pair"}:
                raise HTTPException(422, "戦闘クイズの種類が見つかりません。")
            settings = await get_battle_settings()
            run.answerSeconds = settings.seconds(kind)
            run.answerDeadline = int(time() * 1000) + run.answerSeconds * 1000
    elif run:
        run.answerDeadline = None
        run.answerSeconds = None
    state = GameState(revision=update.revision + 1, save=update.save)
    try:
        rows = await compare_and_save(user_id, update, state)
    except TransientError as cause:
        if cause.code != "Neo.TransientError.Transaction.DeadlockDetected":
            raise
        raise HTTPException(
            409,
            "同時に冒険が更新されました。再読み込みしてください。",
        ) from cause
    if not rows:
        raise HTTPException(
            409,
            "冒険状態または冒険権が別の画面で更新されました。最新の状態を読み直してください。",
        )
    return state


def validate_snapshot_battle(previous: GameState, update: StateUpdate) -> None:
    """専用API以外から戦闘マーカーを作成・削除させない."""
    if update.save.battle is not None:
        raise HTTPException(422, "戦闘マーカーは遭遇APIで発行します。")
    if previous.save.battle:
        raise HTTPException(409, "戦闘中はターン精算または撤退を使用してください。")


def merge_exploration(previous: GameSave, updated: GameSave) -> None:
    """旧クライアントによる未対応フィールドの消失を防ぐ."""
    updated.maps = {**previous.maps, **updated.maps}
    updated.dungeons = {**previous.dungeons, **updated.dungeons}
    if updated.run:
        updated.dungeons.pop(updated.run.resourceId, None)
    if any(key != value.run.resourceId for key, value in updated.dungeons.items()):
        raise HTTPException(422, "ダンジョンの保存先が一致しません。")
    if len(updated.model_dump_json(by_alias=True).encode()) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(422, "冒険状態が大きすぎます。")


async def compare_and_save(
    user_id: UUIDy,
    update: StateUpdate,
    state: GameState,
) -> list:
    """更新番号と冒険権をロック下で比較して同時に更新する."""
    rows, _ = await adb.cypher_query(
        """MATCH (u:User {uid: $uid})
        SET u.adventure_access_lock = coalesce(u.adventure_access_lock, 0) + 1
        WITH u
        WHERE coalesce(u.game_revision, 0) = $revision
          AND (NOT $consume OR coalesce(u.adventure_consumed_slot, -1) < $slot)
        SET u.game_state = $state, u.game_revision = $next_revision,
            u.adventure_consumed_slot = CASE WHEN $consume THEN $slot
                ELSE coalesce(u.adventure_consumed_slot, -1) END
        RETURN u.uid""",
        {
            "uid": to_uuid(user_id).hex,
            "revision": update.revision,
            "next_revision": state.revision,
            "consume": update.consume_access,
            "slot": int(time() // SLOT_SECONDS),
            "state": state.model_dump_json(by_alias=True),
        },
    )
    return rows
