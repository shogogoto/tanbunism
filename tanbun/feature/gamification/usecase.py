"""学習進捗のusecase."""

from datetime import datetime
from zoneinfo import ZoneInfo

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.learning_activity.domain import LearningActivityCounts

from .domain import LearningProgress, XpBreakdown, calculate_learning_progress


async def fetch_learning_progress(user_id: UUIDy) -> LearningProgress:
    """リソース別XP台帳を合算する。削除済み・他人の本の本人実績も保持する."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (event:ResourceXpEvent {user_id: $user_id})
        WHERE event.source IN ['tanbun_exposure', 'quiz_answer', 'correct_bonus']
        RETURN event.source, count(event), sum(event.xp),
            sum(CASE WHEN event.earned_on = $today THEN event.xp ELSE 0 END)
        """,
        params={
            "user_id": to_uuid(user_id).hex,
            "today": datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat(),
        },
    )
    counts = {source: count for source, count, _, _ in rows}
    xp = XpBreakdown(**{source: amount for source, _, amount, _ in rows})
    activity = LearningActivityCounts(
        n_tanbun_exposure=counts.get("tanbun_exposure", 0),
        n_quiz_answered=counts.get("quiz_answer", 0),
        n_quiz_correct=counts.get("correct_bonus", 0),
    )
    return calculate_learning_progress(activity, recorded_xp=xp).model_copy(
        update={"today_xp": sum(today_xp for _, _, _, today_xp in rows)},
    )
