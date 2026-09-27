"""custom middleware."""

from __future__ import annotations

import logging
from typing import override

from fastapi import status
from neomodel.async_.core import AsyncDatabase
from starlette.middleware.base import BaseHTTPMiddleware

from tanbun.config.env import Settings

STATELESS_PATH_PREFIXES = ("/health", "/language")


class Neo4jTransactionMiddleware(BaseHTTPMiddleware):
    """API失敗時にロールバック."""

    @override
    async def dispatch(self, request, call_next):
        if self._should_skip(request.url.path):
            return await call_next(request)
        db = AsyncDatabase()
        await db.begin()
        try:
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
        except Exception:
            await db.rollback()
            logging.exception("Transaction rolled back due to error")  # noqa: LOG015
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
