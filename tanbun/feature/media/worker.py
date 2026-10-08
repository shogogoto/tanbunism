"""One image at a time, leased across processes and retried after restart."""

import asyncio
import contextlib
import logging
from contextvars import Context
from uuid import uuid4

from neomodel import config
from neomodel.async_.core import AsyncDatabase

from tanbun.config.env import Settings

from .cloudinary import CloudinaryClient, configured
from .repo import referenced_ids

logger = logging.getLogger(__name__)


async def process_next(database, *, public_id: str | None = None) -> bool:
    """Short claim transaction; Cloudinary is not called inside it."""
    settings = Settings()
    if not configured(settings):
        return False
    token = uuid4().hex
    rows, _ = await database.cypher_query(
        """MERGE (q:ImageCleanupQueue {key:$cloud})
        SET q.lock=coalesce(q.lock,0)+1
        WITH q WHERE q.lease IS NULL OR q.lease<datetime()
        MATCH (j:ImageCleanup)
        WHERE j.cloud=$cloud AND j.due<=datetime()
            AND ($public_id IS NULL OR j.public_id=$public_id)
            AND (j.lease IS NULL OR j.lease<datetime())
        WITH q, j ORDER BY j.due LIMIT 1
        SET q.lease=datetime()+duration({seconds:120}), q.token=$token
        SET j.lease=datetime()+duration({seconds:120}), j.token=$token
        RETURN j.key, j.public_id, j.attempts""",
        params={
            "cloud": settings.CLOUDINARY_CLOUD_NAME,
            "token": token,
            "public_id": public_id,
        },
    )
    if not rows:
        return False
    key, public_id, attempts = rows[0]
    try:
        # Used uploads survive; replaced/deleted avatars get a new cleanup job.
        if public_id not in await referenced_ids(database):
            await CloudinaryClient(settings).destroy(public_id)
        await database.cypher_query(
            "MATCH (j:ImageCleanup {key:$key, token:$token}) DELETE j",
            params={"key": key, "token": token},
        )
    except Exception:
        logger.warning("Image cleanup deferred for %s", key, exc_info=True)
        await database.cypher_query(
            """MATCH (j:ImageCleanup {key:$key, token:$token})
            SET j.attempts=coalesce(j.attempts,0)+1,
                j.due=datetime()+duration({seconds:$delay}), j.lease=null,
                j.last_error='Cloudinary deletion failed; retry scheduled'""",
            params={
                "key": key,
                "token": token,
                "delay": min(86400, 60 * 2 ** min(attempts, 10)),
            },
        )
    finally:
        await database.cypher_query(
            """MATCH (q:ImageCleanupQueue {key:$cloud, token:$token})
            SET q.lease=null, q.token=null""",
            params={"cloud": settings.CLOUDINARY_CLOUD_NAME, "token": token},
        )
    return True


async def run_worker() -> None:
    """Use a separate context without inherited HTTP DB transactions."""
    database = AsyncDatabase()
    try:
        while True:
            try:
                if not configured(Settings()):
                    await asyncio.sleep(30)
                    continue
                if not database.driver:
                    await database.set_connection(url=config.DATABASE_URL)
                await process_next(database)
            except Exception:
                logger.warning("Image cleanup polling failed; retrying", exc_info=True)
            await asyncio.sleep(30)
    finally:
        await database.close_connection()


def start_worker() -> asyncio.Task:
    """Start with no inherited request context."""
    return asyncio.create_task(run_worker(), context=Context())


async def stop_worker(task: asyncio.Task) -> None:
    """Leases expire after interrupted calls, including process restarts."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
