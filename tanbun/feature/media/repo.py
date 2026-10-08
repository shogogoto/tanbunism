"""Persist cleanup in the same DB transaction as avatar/account changes."""

from neomodel import adb
from neomodel.async_.core import AsyncDatabase

from tanbun.config.after_commit import after_commit
from tanbun.config.env import Settings

from .cloudinary import avatar_id, managed_id


async def schedule_delete(public_id: str, *, delay: int = 3600, database=adb) -> None:
    """Hour-long signature grace; UUID per upload prevents replacement races."""
    settings = Settings()
    if not managed_id(public_id, settings):
        return
    await database.cypher_query(
        """MERGE (job:ImageCleanup {key:$key})
        ON CREATE SET job.public_id=$id, job.cloud=$cloud, job.attempts=0,
            job.created_at=datetime(), job.due=datetime()+duration({seconds:$delay})
        ON MATCH SET job.due=CASE
            WHEN job.due > datetime()+duration({seconds:$delay})
            THEN datetime()+duration({seconds:$delay}) ELSE job.due END
        """,
        params={
            "key": f"{settings.CLOUDINARY_CLOUD_NAME}:{public_id}",
            "id": public_id,
            "cloud": settings.CLOUDINARY_CLOUD_NAME,
            "delay": delay,
        },
    )


async def schedule_avatar_delete(url: str | None, *, delay: int = 3600) -> None:
    """No destructive fallback when URL identity is unknown."""
    public_id = avatar_id(url, Settings())
    if public_id:
        await schedule_delete(public_id, delay=delay)
        if delay == 0:

            async def delete_replaced_avatar() -> None:
                # Import lazily: the worker also uses this repository.
                from .worker import process_next  # noqa: PLC0415

                await process_next(AsyncDatabase(), public_id=public_id)

            after_commit(delete_replaced_avatar)


async def referenced_ids(database=adb) -> set[str]:
    """Compare identities rather than crop/quality/version URL strings."""
    rows, _ = await database.cypher_query(
        "MATCH (u:User) WHERE u.avatar_url IS NOT NULL RETURN u.avatar_url",
    )
    settings = Settings()
    return {
        public_id
        for row in rows
        if (public_id := avatar_id(row[0], settings, managed_only=False))
    }
