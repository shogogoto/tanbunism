"""ゲームと学習計画が共有する、DBに保存したクイズ準備状態."""

from datetime import datetime, timedelta
from typing import Literal

from neomodel import adb
from pydantic import BaseModel

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import UUIDy, to_uuid

from .preparation_control import PREPARATION_TIMEOUT_SECONDS

PreparationStatus = Literal["idle", "queued", "running", "ready", "failed", "exhausted"]


class PreparationState(BaseModel, frozen=True):
    """準備済み数と実際のジョブ状態を混同しない."""

    status: PreparationStatus = "idle"
    required_quiz_count: int = 0
    error: str | None = None


async def preparation_states(user_id: UUIDy) -> dict[str, PreparationState]:
    """再起動で残った実行中表示は失敗として返し、再試行可能にする."""
    rows, _ = await adb.cypher_query(
        """MATCH (p:StudyPlan)-[:OWNED]->(:User {uid:$uid})
        RETURN p.uid, p.preparation_status, p.preparation_required,
               p.preparation_error, toString(p.preparation_updated)""",
        {"uid": to_uuid(user_id).hex},
    )
    result = {}
    cutoff = datetime.now(TZ) - timedelta(seconds=PREPARATION_TIMEOUT_SECONDS)
    for uid, stored_status, required, stored_error, updated in rows:
        status = stored_status or "idle"
        error = stored_error
        if status in {"queued", "running"} and (
            not updated or datetime.fromisoformat(updated) < cutoff
        ):
            status, error = (
                "failed",
                "準備処理が中断されたか、時間がかかりすぎています。再試行してください。",
            )
        result[str(uid)] = PreparationState(
            status=status,
            required_quiz_count=required or 0,
            error=error,
        )
    return result


async def set_preparation_state(
    plan_id: UUIDy,
    user_id: UUIDy,
    status: PreparationStatus,
    *,
    required: int | None = None,
    error: str | None = None,
) -> PreparationState:
    """進捗はStudyPlanに保存し、ゲーム専用の実行フラグを作らない."""
    rows, _ = await adb.cypher_query(
        """MATCH (p:StudyPlan {uid:$plan})-[:OWNED]->(:User {uid:$uid})
        SET p.preparation_status=$status,
            p.preparation_required=coalesce($required,p.preparation_required,0),
            p.preparation_error=$error, p.preparation_updated=datetime($now)
        RETURN p.preparation_required""",
        {
            "plan": to_uuid(plan_id).hex,
            "uid": to_uuid(user_id).hex,
            "status": status,
            "required": required,
            "error": error[:500] if error else None,
            "now": datetime.now(TZ).isoformat(),
        },
    )
    return PreparationState(
        status=status,
        required_quiz_count=rows[0][0] if rows else required or 0,
        error=error,
    )
