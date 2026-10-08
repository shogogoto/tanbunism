"""管理者付与は管理者専用・メール確認必須。既存の認証に即時適用."""

from uuid import uuid4

from httpx import AsyncClient
from starlette import status

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister


@mark_async_test()
async def test_admin_grants_confirmed_active_user(ac: AsyncClient):
    """既存のJWTでも付与後は管理APIを利用でき、再送も安全."""
    target = await aregister("role-target@example.com")
    target_headers = await aauth_header(target.email)
    admin = await aregister("role-admin@example.com")
    admin.is_superuser = True
    await admin.save()
    headers = await aauth_header(admin.email)
    path = f"/admin/users/{target.uid}/admin"
    body = {"confirmation": target.email}
    assert (await ac.post(path, json=body)).status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        await ac.post(path, json=body, headers=target_headers)
    ).status_code == status.HTTP_403_FORBIDDEN
    await ac.patch("/user/me", headers=target_headers, json={"is_superuser": True})
    assert (
        await ac.get("/admin/users", headers=target_headers)
    ).status_code == status.HTTP_403_FORBIDDEN
    wrong = await ac.post(path, json={"confirmation": admin.email}, headers=headers)
    assert wrong.status_code == status.HTTP_400_BAD_REQUEST
    await target.refresh()
    assert not target.is_superuser
    target.is_active = False
    await target.save()
    assert (
        await ac.post(path, json=body, headers=headers)
    ).status_code == status.HTTP_409_CONFLICT
    target.is_active = True
    await target.save()
    result = await ac.post(path, json=body, headers=headers)
    assert result.status_code == status.HTTP_200_OK
    assert result.json()["is_superuser"] is True
    assert (
        await ac.get("/admin/users", headers=target_headers)
    ).status_code == status.HTTP_200_OK
    assert (
        await ac.post(path, json=body, headers=headers)
    ).status_code == status.HTTP_200_OK
    missing = await ac.post(f"/admin/users/{uuid4()}/admin", json=body, headers=headers)
    assert missing.status_code == status.HTTP_404_NOT_FOUND
