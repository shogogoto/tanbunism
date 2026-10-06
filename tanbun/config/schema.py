"""Install the Neo4j schema declared by neomodel labels."""

from __future__ import annotations

import asyncio

from neomodel import adb

from tanbun.config.env import Settings
from tanbun.feature.achievement.label import LArchievement
from tanbun.feature.entry.label import (
    LEntry,
    LFolder,
    LHead,
    LResource,
    LResourceStatsCache,
)
from tanbun.feature.gamification.label import LResourceXpEvent
from tanbun.feature.quiz.label import LAnswer, LQuiz
from tanbun.feature.tanbun.label import LInterval, LQuoterm, LSentence, LTerm
from tanbun.feature.user.label import LAccount, LUser

ASYNC_LABELS = (
    LAccount,
    LUser,
    LHead,
    LEntry,
    LResource,
    LResourceStatsCache,
    LFolder,
    LQuiz,
    LAnswer,
    LArchievement,
    LSentence,
    LTerm,
    LQuoterm,
    LInterval,
    LResourceXpEvent,
)


async def install_schema() -> None:
    """Install indexes and constraints for every declared node label."""
    settings = Settings()
    settings.setup_db()

    try:
        for label in ASYNC_LABELS:
            await adb.install_labels(label, quiet=False)
    finally:
        await adb.close_connection()


def main() -> None:
    """Install the schema from a command-line process."""
    asyncio.run(install_schema())


if __name__ == "__main__":
    main()
