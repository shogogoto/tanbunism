"""oauth."""

from typing import override

from fastapi import Response
from fastapi_users import models
from fastapi_users.authentication import (
    AuthenticationBackend,
    Strategy,
)

from tanbun.config.env import Settings


class RedirectCookieAuthentication(AuthenticationBackend):
    """cookie認証後のリダイレクト先を設定する."""

    @override
    async def login(
        self,
        strategy: Strategy[models.UP, models.ID],
        user: models.UP,
    ) -> Response:
        res = await super().login(strategy, user)
        settings = Settings()
        if settings.FRONTEND_URL is not None:
            res.status_code = 303
            res.headers["Location"] = settings.FRONTEND_URL
        return res


def google_cookie_backend() -> AuthenticationBackend:
    """For fastapi-users."""
    from tanbun.feature.user.backend import (  # noqa: PLC0415
        get_cookie_transport,
        get_strategy,
    )

    return RedirectCookieAuthentication(
        name="cookie",
        transport=get_cookie_transport(),
        get_strategy=get_strategy,
    )
