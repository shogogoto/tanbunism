"""root api."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from neomodel.async_.core import AsyncDatabase

from tanbun.api.middleware.error_handling import ErrorHandlingMiddleware
from tanbun.api.middleware.logging import LoggingMiddleware
from tanbun.api.middleware.logging.log_config import setup_logging
from tanbun.api.middleware.transaction import Neo4jTransactionMiddleware
from tanbun.config.env import Settings
from tanbun.feature.achievement.router.router import user_achievement_router
from tanbun.feature.entry.router import entry_router
from tanbun.feature.gamification import gamification_router
from tanbun.feature.quiz.router.router import quiz_router
from tanbun.feature.tanbun.router import tanbun_router
from tanbun.feature.user import PREFIX_USER
from tanbun.feature.user.routers import auth_router, user_router

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


s = Settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator:
    """Set up DB etc."""
    s.setup_db()
    yield
    await AsyncDatabase().close_connection()


api = FastAPI(lifespan=lifespan)

api.add_middleware(ErrorHandlingMiddleware)
api.add_middleware(
    Neo4jTransactionMiddleware,
    # paths=["/api/v1"], 適用パス
)
api.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=s.allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
api.add_middleware(LoggingMiddleware)

api.include_router(auth_router())
api.include_router(user_router())
api.include_router(
    user_achievement_router(),
    prefix=PREFIX_USER,
    tags=["public_user"],
)
api.include_router(entry_router())
api.include_router(gamification_router())
api.include_router(tanbun_router(), prefix="/tanbun", tags=["tanbun"])
api.include_router(quiz_router())


def is_pytest_running() -> bool:  # noqa: D103
    return "pytest" in sys.modules


if not is_pytest_running():
    setup_logging()


def root_router() -> FastAPI:  # noqa: D103
    return api


@api.get("/health")
async def check_health() -> str:
    """Check health."""
    return "ok"
