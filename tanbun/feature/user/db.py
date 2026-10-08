"""user CRUD on fastapi-users."""

from __future__ import annotations

from typing import override
from uuid import UUID

from fastapi import HTTPException
from fastapi_users.db import (
    BaseUserDatabase,
)

from tanbun.config.env import Settings
from tanbun.feature.media.cloudinary import avatar_id
from tanbun.feature.media.repo import schedule_avatar_delete
from tanbun.feature.user.domain import Account, User
from tanbun.feature.user.label import LAccount, LUser


async def _get_user_with_account(**kwargs) -> User | None:
    lb: LUser = await LUser.nodes.get_or_none(**kwargs)
    if lb is None:
        return None
    accounts = [
        Account.model_validate(ac.__properties__) for ac in await lb.accounts.all()
    ]
    return User.model_validate({**lb.__properties__, "oauth_accounts": accounts})


class AccountDB(BaseUserDatabase[User, UUID]):
    """DB adapter for fastapi-users."""

    @override
    async def get(self, id):
        return await _get_user_with_account(uid=id.hex)

    @override
    async def get_by_email(self, email):
        return await _get_user_with_account(email=email)

    @override
    async def get_by_oauth_account(self, oauth, account_id):
        la: LAccount | None = await LAccount.nodes.get_or_none(account_id=account_id)
        if la is None:
            return None
        lu: LUser = await la.user.single()
        return User.model_validate(lu.__properties__)

    @override
    async def create(self, create_dict):
        if await self.get_by_email(create_dict["email"]):
            raise  # noqa: PLE0704
        lb = await LUser(**create_dict).save()
        return User.model_validate(lb.__properties__)

    @override
    async def update(self, user, update_dict):
        lb = await LUser.nodes.get(uid=user.id.hex)
        if "avatar_url" in update_dict and update_dict["avatar_url"] is not None:
            settings = Settings()
            new_url = update_dict["avatar_url"]
            public_id = avatar_id(new_url, settings)
            if public_id:
                owner = public_id[len(settings.CLOUDINARY_AVATAR_FOLDER) + 1 :].split(
                    "/",
                )[0]
                if UUID(owner) != user.id:
                    raise HTTPException(400, "他のユーザーの画像は設定できません。")
            if new_url != lb.avatar_url and public_id != avatar_id(
                lb.avatar_url,
                settings,
            ):
                # Persist with the avatar update; the worker only sees committed jobs.
                # Fresh upload IDs allow cleanup without a replacement grace period.
                await schedule_avatar_delete(lb.avatar_url, delay=0)
        for k, v in update_dict.items():
            if v is None:
                continue
            setattr(lb, k, v)
        lb = await lb.save()
        return User.model_validate(lb.__properties__)

    @override
    async def delete(self, user):
        lb = await LUser.nodes.get(uid=user.id.hex)
        await schedule_avatar_delete(lb.avatar_url)
        await lb.delete()

    # OAUTH BaseUserManager.oauth_callbackで使用される
    @override
    async def add_oauth_account(self, user, create_dict):
        la = await LAccount(**create_dict).save()
        lu: LUser = await LUser.nodes.get(uid=user.id.hex)
        await lu.accounts.connect(la)
        return user

    @override
    async def update_oauth_account(self, user, oauth_account, update_dict):
        la = LAccount.nodes.get(account_id=oauth_account.account_id)
        for k, v in update_dict.items():
            if v is None:
                continue
            setattr(la, k, v)
        la.save()
        return user
