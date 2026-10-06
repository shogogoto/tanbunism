"""fastapi error handling middleware."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from tanbun.config.database import is_database_timeout

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

logger = logging.getLogger(__name__)


def database_timeout_response() -> JSONResponse:
    """期限超過の応答をDB例外の検出場所によらず共通化."""
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={
            "detail": {
                "code": 504,
                "message": "データベース処理が時間上限を超えたため中止しました。",
            },
        },
    )


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """fastapiでエラーを検出."""

    @override
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        try:
            return await call_next(request)
        except HTTPException as e:
            if await request.is_disconnected():
                return JSONResponse(
                    status_code=e.status_code,
                    content=e.detail,
                )
            return JSONResponse(
                status_code=e.status_code,
                content=e.detail,
                headers=e.headers,
            )
        except Exception as e:
            logger.exception("Uncaught exception %s", e.__class__.__name__)
            if is_database_timeout(e):
                return database_timeout_response()
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=[str(arg) for arg in e.args],
            )
