"""custom middleware."""

from __future__ import annotations

import logging
from typing import override

from fastapi import status
from neomodel.async_.core import AsyncDatabase
from starlette.middleware.base import BaseHTTPMiddleware

from tanbun.api.middleware.error_handling import database_timeout_response
from tanbun.config.database import database_budget, is_database_timeout
from tanbun.config.env import Settings

STATELESS_PATH_PREFIXES = ("/health", "/language")


class Neo4jTransactionMiddleware(BaseHTTPMiddleware):
    """API失敗時にロールバック."""

    @override
    async def dispatch(self, request, call_next):
        settings = Settings()
        timeout = (
            settings.NEO4J_READ_TIMEOUT_SECONDS
            if request.method in {"GET", "HEAD", "OPTIONS"}
            else settings.NEO4J_WRITE_TIMEOUT_SECONDS
        )
        with database_budget(timeout, f"{request.method} {request.url.path}"):
            return await self._dispatch_transaction(request, call_next)

    async def _dispatch_transaction(self, request, call_next):
        if self._should_skip(request.url.path):
            return await call_next(request)
        db = AsyncDatabase()
        try:
            await db.begin()
            res = await call_next(request)
            if status.HTTP_200_OK <= res.status_code < status.HTTP_300_MULTIPLE_CHOICES:
                await db.commit()
            else:
                await db.rollback()
                logger = logging.getLogger("neo4j_transaction")
                logger.warning(
                    "Transaction rolled back due to response status %s",
                    res.status_code,
                )
        except BaseException as error:
            # キャンセル時も接続・トランザクションを残さない。
            if db._active_transaction is not None:  # noqa: SLF001
                try:
                    await db.rollback()
                except Exception:
                    logging.exception("Rollback failed while handling DB error")  # noqa: LOG015
            logging.exception("Transaction rolled back due to error")  # noqa: LOG015
            if isinstance(error, Exception) and is_database_timeout(error):
                return database_timeout_response()
            raise
        return res

    @classmethod
    def _should_skip(cls, path: str) -> bool:
        s = Settings()
        if path.startswith(STATELESS_PATH_PREFIXES):
            return True
        return any(
            path.startswith(exclude_path)
            for exclude_path in s.neo4j_transaction_exclude_paths
        )
