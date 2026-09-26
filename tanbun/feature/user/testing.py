"""テスト用認証etc."""

from collections.abc import AsyncGenerator

from httpx import ASGITransport, AsyncClient
from neomodel import StringProperty

from tanbun.api import api
from tanbun.config.env import Settings
from tanbun.feature.user.label import LowerEmailProperty, LUser


async def async_client() -> AsyncGenerator[AsyncClient]:  # noqa: D103
    s = Settings()
    async with AsyncClient(
        transport=ASGITransport(app=api, raise_app_exceptions=False),
        base_url=s.KNOWDE_URL,
    ) as client:
        yield client


_PW = "password"

type Stry = str | StringProperty | LowerEmailProperty


async def async_auth_header(email: Stry = "one@gmail.com") -> dict[str, str]:  # noqa: D103
    await aregister(email)
    return await aauth_header(email)


async def aregister(  # noqa: D103
    email: Stry = "one@gmail.com",
) -> LUser:
    async for ac in async_client():
        d = {"email": email, "password": _PW}
        await ac.post("/auth/register", json=d)
    return await LUser.nodes.get(email=email)


async def aauth_header(email: Stry = "one@gmail.com") -> dict[str, str]:  # noqa: D103
    async for ac in async_client():
        d = {"username": email, "password": _PW}
        res = await ac.post("/auth/jwt/login", data=d)
        token = res.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}
    msg = "async_auth_header"
    raise AssertionError(msg)  # 到達しないはず
