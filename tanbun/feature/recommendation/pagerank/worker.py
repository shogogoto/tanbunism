"""専用の永続FIFOキュー。ブラウザを閉じても処理を続ける."""

import asyncio
import contextlib
import json
import logging
from contextvars import Context
from uuid import uuid4

from neomodel import config
from neomodel.async_.core import AsyncDatabase

from tanbun.feature.notification.domain import NewNotification, NotificationKind
from tanbun.feature.notification.usecase import notify_user

from .domain import calculate_ranks
from .repo import PageRankStore

logger = logging.getLogger(__name__)


async def process_job(store: PageRankStore, job: dict) -> None:
    """リソースを一つずつ計算。一件失敗しても次に進む."""
    results = json.loads(job["results"])
    for resource_id in job["resource_ids"][len(results) :]:
        if not await store.heartbeat(job["uid"], job["owner"]):
            return
        title = resource_id
        error = None
        try:
            snapshot = await store.graph(resource_id, await store.settings())
            title = snapshot[0]
            await store.current(job, title)
            ranks = await asyncio.to_thread(calculate_ranks, snapshot[3], snapshot[4])
            await store.publish(job, resource_id, snapshot, ranks)
        except ValueError as exc:
            error = str(exc)[:300]
        except Exception:
            logger.exception("PageRank failed for resource %s", resource_id)
            error = "計算・DB通信に失敗しました。このリソースを再計算してください。"
        results.append({"resource_id": resource_id, "title": title, "error": error})
        if not await store.checkpoint(job, results):
            return
    if not await store.checkpoint(job, results, finished=True):
        return
    failures = sum(item["error"] is not None for item in results)
    try:
        await notify_user(
            job["user_id"],
            NewNotification(
                kind=NotificationKind.PAGERANK_COMPLETE,
                title="PageRankの一括計算が完了しました",
                description=(
                    f"成功 {len(results) - failures}件 / 失敗 {failures}件。"
                    "adminで結果を確認できます。"
                ),
                href="/admin?view=pagerank",
            ),
        )
    except Exception:
        logger.exception("PageRank completion notification failed")


async def _leased_job(store: PageRankStore, job: dict) -> None:
    async def keep_alive() -> None:
        while True:
            await asyncio.sleep(20)
            if not await store.heartbeat(job["uid"], job["owner"]):
                msg = "PageRank lease lost"
                raise RuntimeError(msg)

    # Either failed heartbeat or shutdown cancels the computation; never publish
    # without a live lease. CPU work has a finite iteration bound.
    async with asyncio.TaskGroup() as group:
        heartbeat = group.create_task(keep_alive())
        try:
            await process_job(store, job)
        finally:
            heartbeat.cancel()


async def run_worker() -> None:
    """DB単独で待機・再開する。HTTPのトランザクションを共有しない."""
    database = AsyncDatabase()
    store = PageRankStore(database)
    active: set[asyncio.Task] = set()
    try:
        while True:
            for task in list(active):
                if task.done():
                    active.remove(task)
                    try:
                        task.result()
                    except Exception:
                        logger.exception(
                            "PageRank worker interrupted; lease will be retried",
                        )
            try:
                if not database.driver:
                    await database.set_connection(url=config.DATABASE_URL)
                settings = await store.settings()
                if len(active) < settings.max_concurrent_jobs:
                    job = await store.claim(uuid4().hex, settings.max_concurrent_jobs)
                    if job:
                        active.add(asyncio.create_task(_leased_job(store, job)))
                        continue
            except Exception:
                logger.exception("PageRank queue polling failed; retrying")
            await asyncio.sleep(5)
    finally:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        await database.close_connection()


def start_worker() -> asyncio.Task:
    """新しいコンテキストで開始し、HTTP期限・認証情報を継承しない."""
    return asyncio.create_task(run_worker(), context=Context())


async def stop_worker(task: asyncio.Task) -> None:
    """終了時はリースを残して次の起動に引き継ぐ."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
