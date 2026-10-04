"""一括クイズ準備の運用設定."""

from neomodel import adb
from pydantic import BaseModel, Field


class QuizPreparationSettings(BaseModel, frozen=True):
    """一括クイズ準備のサーバー制限."""

    max_concurrent_jobs: int = Field(ge=1, le=8, default=1)
    max_concurrent_jobs_per_user: int = Field(ge=1, le=4, default=1)
    max_quizzes_per_job: int = Field(ge=1, le=2000, default=500)


async def get_quiz_preparation_settings() -> QuizPreparationSettings:
    """一括クイズ準備の制限を取得し、未作成なら既定値で作る."""
    defaults = QuizPreparationSettings()
    rows, _ = await adb.cypher_query(
        """
        MERGE (settings:AdminSettings {key: 'quiz_preparation'})
        ON CREATE SET
            settings.max_concurrent_jobs = $max_concurrent_jobs,
            settings.max_concurrent_jobs_per_user =
                $max_concurrent_jobs_per_user,
            settings.max_quizzes_per_job = $max_quizzes_per_job
        RETURN settings.max_concurrent_jobs,
            settings.max_concurrent_jobs_per_user,
            settings.max_quizzes_per_job
        """,
        params=defaults.model_dump(),
    )
    return QuizPreparationSettings(
        max_concurrent_jobs=rows[0][0],
        max_concurrent_jobs_per_user=rows[0][1],
        max_quizzes_per_job=rows[0][2],
    )


async def update_quiz_preparation_settings(
    settings: QuizPreparationSettings,
) -> QuizPreparationSettings:
    """一括クイズ準備の制限を保存する."""
    rows, _ = await adb.cypher_query(
        """
        MERGE (current:AdminSettings {key: 'quiz_preparation'})
        SET current.max_concurrent_jobs = $max_concurrent_jobs,
            current.max_concurrent_jobs_per_user =
                $max_concurrent_jobs_per_user,
            current.max_quizzes_per_job = $max_quizzes_per_job
        RETURN current.max_concurrent_jobs,
            current.max_concurrent_jobs_per_user,
            current.max_quizzes_per_job
        """,
        params=settings.model_dump(),
    )
    return QuizPreparationSettings(
        max_concurrent_jobs=rows[0][0],
        max_concurrent_jobs_per_user=rows[0][1],
        max_quizzes_per_job=rows[0][2],
    )
