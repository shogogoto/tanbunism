"""公開学習進捗APIのテスト."""

from httpx import AsyncClient

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aregister


@mark_async_test()
async def test_get_learning_progress_without_login(ac: AsyncClient) -> None:
    """公開プロフィールではログインせずLevelとXPを取得できる."""
    user = await aregister("learning-progress@example.com")

    response = await ac.get(f"/user/{user.uid}/learning-progress")

    assert response.is_success
    assert response.json()["level"] == 1
    assert response.json()["total_xp"] == 0
    assert response.json()["xp_details"] == [
        {
            "source": "knowledge",
            "activity_count": 0,
            "xp_per_activity": 1,
            "earned_xp": 0,
        },
        {
            "source": "quiz_creation",
            "activity_count": 0,
            "xp_per_activity": 1,
            "earned_xp": 0,
        },
        {
            "source": "quiz_answer",
            "activity_count": 0,
            "xp_per_activity": 5,
            "earned_xp": 0,
        },
        {
            "source": "correct_bonus",
            "activity_count": 0,
            "xp_per_activity": 2,
            "earned_xp": 0,
        },
    ]
