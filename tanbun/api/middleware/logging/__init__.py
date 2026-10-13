"""fastapiログ用."""

import logging
import time
import uuid
from typing import override

from fastapi import Request
from fastapi_users.jwt import decode_jwt
from jwt import PyJWTError
from starlette.middleware.base import BaseHTTPMiddleware

from tanbun.feature.user.backend import get_strategy

from .context import method_var, request_id_var, url_var, user_id_var


def req2user_id(request: Request) -> str | None:  # noqa: D103
    jwt: str | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        jwt = auth_header.split(" ")[1]
    else:
        jwt = request.cookies.get("fastapiusersauth")

    if jwt:
        strategy = get_strategy()
        try:
            data = decode_jwt(
                jwt,
                strategy.decode_key,
                strategy.token_audience,
                algorithms=[strategy.algorithm],
            )
            return uuid.UUID(data["sub"]).hex
        except (PyJWTError, ValueError, TypeError, KeyError):
            return None
    return None


logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """ログ用."""

    @override
    async def dispatch(self, request, call_next):
        req_id = str(uuid.uuid4())
        url_path = request.url.path
        method = request.method
        rid_token = request_id_var.set(req_id)
        url_token = url_var.set(url_path)
        method_token = method_var.set(method)
        user_id_token = user_id_var.set(None)
        try:
            try:
                user_id = req2user_id(request)
                user_id_var.set(user_id)
            except Exception:
                logger.exception("Could not identify user for request logging")
                user_id = None
            start_time = time.time()
            response = await call_next(request)
            process_time = time.time() - start_time

            response.headers["X-Process-Time"] = str(process_time)
            response.headers["X-Request-Id"] = req_id
            response.headers["X-URL"] = url_path
            if user_id:
                response.headers["X-User-Id"] = user_id

            logger.info("Success in %.4fs", process_time)
            return response
        finally:
            request_id_var.reset(rid_token)
            url_var.reset(url_token)
            method_var.reset(method_token)
            user_id_var.reset(user_id_token)
