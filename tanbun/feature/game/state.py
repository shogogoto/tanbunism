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

from .access import SLOT_SECONDS

MAX_SNAPSHOT_BYTES = 1000000


class Run(BaseModel):
    """保存時に検査する攻略中の状態."""

    resourceId: str = Field(max_length=64)
    name: str = Field(max_length=1000)
    hp: int = Field(ge=0, le=100000)
    maxHp: int = Field(ge=1, le=100000)
    attack: int = Field(ge=0, le=100000)
    defense: int = Field(ge=0, le=100000)
    moves: int = Field(ge=0, le=5)
    kills: int = Field(ge=0, le=3)
    enemyHp: int = Field(ge=0, le=100000)
    enemyMaxHp: int = Field(ge=1, le=100000)
    quizCursor: int = Field(ge=0)
    readIds: list[str] = Field(max_length=100)
    phase: Literal["path", "battle", "rest", "defeated", "cleared"]


class GameSave(BaseModel):
    """攻略数と固定された出題セット."""

    version: Literal[2] = 2
    clears: dict[str, int] = Field(default_factory=dict, max_length=1000)
    run: Run | None = None
    content: dict | None = None

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
    return GameState.model_validate_json(rows[0][0]) if rows[0][0] else GameState()


async def write_state(user_id: UUIDy, update: StateUpdate) -> GameState:
    """別端末の更新を上書きせず、保存と入場権を同時に確定する."""
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
            "state": state.model_dump_json(),
        },
    )
    return rows
