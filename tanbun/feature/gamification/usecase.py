"""学習進捗のusecase."""

from datetime import datetime
from zoneinfo import ZoneInfo

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.learning_activity.domain import LearningActivityCounts

from .domain import LearningProgress, XpBreakdown, calculate_learning_progress
from .settings import get_gamification_settings


async def fetch_learning_progress(user_id: UUIDy) -> LearningProgress:
    """リソース別XP台帳を合算する。削除済み・他人の本の本人実績も保持する."""
    return (await fetch_learning_progresses([user_id]))[to_uuid(user_id).hex]


async def fetch_learning_progresses(
    user_ids: list[UUIDy],
) -> dict[str, LearningProgress]:
    """プロフィールと検索で同じ台帳・係数を使い、ページ単位で一括取得する."""
    ids = list(dict.fromkeys(to_uuid(uid).hex for uid in user_ids))
    if not ids:
        return {}
    rows, _ = await adb.cypher_query(
        """
        UNWIND $user_ids AS user_id
        MATCH (event:ResourceXpEvent {user_id: user_id})
        WHERE event.source IN ['tanbun_exposure', 'quiz_answer', 'correct_bonus']
        RETURN user_id, event.source, count(event), sum(event.xp),
            sum(CASE WHEN event.earned_on = $today THEN event.xp ELSE 0 END)
        """,
        params={
            "user_ids": ids,
            "today": datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat(),
        },
    )
    settings = await get_gamification_settings()
    grouped = {uid: [] for uid in ids}
    for uid, source, count, amount, today_xp in rows:
        grouped[uid].append((source, count, amount, today_xp))
    result = {}
    for uid, events in grouped.items():
        counts = {source: count for source, count, _, _ in events}
        xp = XpBreakdown(**{source: amount for source, _, amount, _ in events})
        activity = LearningActivityCounts(
            n_tanbun_exposure=counts.get("tanbun_exposure", 0),
            n_quiz_answered=counts.get("quiz_answer", 0),
            n_quiz_correct=counts.get("correct_bonus", 0),
        )
        result[uid] = calculate_learning_progress(
            activity,
            recorded_xp=xp,
            coefficient=settings.level_xp_coefficient,
        ).model_copy(
            update={"today_xp": sum(today_xp for _, _, _, today_xp in events)},
        )
    return result
