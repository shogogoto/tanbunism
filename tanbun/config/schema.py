"""Install the Neo4j schema declared by neomodel labels."""

from __future__ import annotations

import asyncio
from typing import Any, TextIO, override
from weakref import WeakKeyDictionary

from neo4j.exceptions import ClientError, TransientError
from neomodel.async_.core import AsyncDatabase

from tanbun.config.database import database_budget
from tanbun.config.env import Settings
from tanbun.feature.achievement.label import LArchievement
from tanbun.feature.entry.label import (
    LEntry,
    LFolder,
    LHead,
    LResource,
    LResourceStatsCache,
)
from tanbun.feature.game.label import LDungeon, LDungeonRegion
from tanbun.feature.gamification.label import LResourceXpEvent
from tanbun.feature.media.label import LImageCleanup, LImageCleanupQueue
from tanbun.feature.quiz.label import LAnswer, LQuiz
from tanbun.feature.recommendation.pagerank.label import LPageRankJob, LPageRankQueue
from tanbun.feature.tanbun.label import LInterval, LQuoterm, LSentence, LTerm
from tanbun.feature.user.label import LAccount, LUser

ASYNC_LABELS = (
    LImageCleanup,
    LImageCleanupQueue,
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
    LPageRankQueue,
    LPageRankJob,
    LDungeon,
    LDungeonRegion,
)

_constraint_locks: WeakKeyDictionary = WeakKeyDictionary()


def _constraint_lock() -> asyncio.Lock:
    # Keep DDL in this process sequential, including callers using different DB
    # instances. Locks belong to their event loop (CLI/tests may use another loop).
    loop = asyncio.get_running_loop()
    if loop not in _constraint_locks:
        _constraint_locks[loop] = asyncio.Lock()
    return _constraint_locks[loop]


def _identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


class SchemaDatabase(AsyncDatabase):
    """neomodelの宣言を使い、制約登録だけを冪等・検証付きにする互換境界."""

    async def _create_idempotent_constraint(self, query: str) -> None:
        # Concurrent IF NOT EXISTS operations can still deadlock while upgrading
        # label locks. Only this idempotent DDL is retried, never application writes
        # or schema-name collisions. The failed auto-transaction is already closed.
        for attempt in range(3):
            try:
                await self.cypher_query(query)
            except TransientError as error:
                if (
                    error.code != "Neo.TransientError.Transaction.DeadlockDetected"
                    or attempt == 2  # noqa: PLR2004
                ):
                    raise
                await asyncio.sleep(0.05 * (attempt + 1))
            else:
                return

    @override
    async def _create_node_constraint(
        self,
        target_cls: Any,
        property_name: str,
        stdout: TextIO,
        quiet: bool,
    ) -> None:
        label = target_cls.__label__
        name = f"constraint_unique_{label}_{property_name}"
        try:
            async with _constraint_lock():
                await self._create_idempotent_constraint(
                    f"CREATE CONSTRAINT {_identifier(name)} IF NOT EXISTS "
                    f"FOR (n:{_identifier(label)}) "
                    f"REQUIRE n.{_identifier(property_name)} IS UNIQUE",
                )
        except ClientError as error:
            if error.code == "Neo.ClientError.Schema.IndexWithNameAlreadyExists":
                msg = (
                    f"Schema collision: {name}. Inspect SHOW INDEXES and SHOW "
                    "CONSTRAINTS; an unfinished or conflicting index may remain. "
                    "No index was deleted automatically."
                )
                raise RuntimeError(
                    msg,
                ) from error
            raise
        # IF NOT EXISTS can also skip a different constraint with the same name.
        # Verify semantics, not merely successful execution or the object's name.
        rows, _ = await self.cypher_query(
            "SHOW CONSTRAINTS YIELD type, entityType, labelsOrTypes, properties "
            "WHERE type IN ['UNIQUENESS', 'NODE_PROPERTY_UNIQUENESS'] "
            "AND entityType = 'NODE' "
            "AND labelsOrTypes = $labels AND properties = $properties "
            "RETURN count(*)",
            {"labels": [label], "properties": [property_name]},
        )
        if not rows or not rows[0][0]:
            msg = f"Required uniqueness constraint is missing: {name}"
            raise RuntimeError(msg)
        if not quiet:
            stdout.write(
                f" + Verified uniqueness constraint: {label}.{property_name}\n",
            )


async def install_schema() -> None:
    """Install indexes and constraints for every declared node label."""
    settings = Settings()
    settings.setup_db()
    database = SchemaDatabase()
    try:
        with database_budget(settings.NEO4J_SCHEMA_TIMEOUT_SECONDS, "schema-install"):
            for label in ASYNC_LABELS:
                await database.install_labels(label, quiet=False)
    finally:
        await database.close_connection()


def main() -> None:
    """Install the schema from a command-line process."""
    asyncio.run(install_schema())


if __name__ == "__main__":
    main()
