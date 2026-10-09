"""Authenticated uploads and explicit admin orphan inspection."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from neomodel import adb
from pydantic import BaseModel, Field

from tanbun.config.env import Settings
from tanbun.feature.media.cloudinary import (
    CloudinaryClient,
    avatar_folder_id,
    managed_id,
    upload_signature,
)
from tanbun.feature.media.repo import (
    referenced_ids,
    schedule_avatar_delete,
    schedule_delete,
)
from tanbun.feature.user.router_util import ActiveUser, AdminUser

router = APIRouter(tags=["images"])
GRACE_SECONDS = 72 * 3600
MAX_UPLOADS_PER_HOUR = 20


class SignUploadRequest(BaseModel):
    """Widget parameters, tightly validated before signing."""

    params: dict[str, Annotated[str, Field(max_length=2048)]] = Field(max_length=12)


class CleanupRequest(BaseModel):
    """Only explicitly selected orphan IDs may be queued."""

    public_ids: list[Annotated[str, Field(max_length=200)]] = Field(
        min_length=1,
        max_length=100,
    )


def eligible(resource: dict, refs: set[str], settings: Settings) -> bool:
    """Leave unknown names, live references and recent uploads untouched."""
    if not managed_id(resource["public_id"], settings) or resource["public_id"] in refs:
        return False
    try:
        created = datetime.fromisoformat(resource["created_at"])
        updated = datetime.fromtimestamp(int(resource.get("version", 0)), UTC)
        return max(created, updated) < datetime.now(UTC) - timedelta(
            seconds=GRACE_SECONDS,
        )
    except (ValueError, KeyError, TypeError):
        return False


@router.post("/user/avatar/sign")
async def sign_avatar(body: SignUploadRequest, user: ActiveUser) -> dict:
    """Authentication/identity validation happens on the API owning the cookie."""
    settings = Settings()
    signature = upload_signature(body.params, user.id, settings)
    # Prevent unbounded storage abuse; a retry of the same signature is harmless.
    public_id = f"{settings.CLOUDINARY_AVATAR_FOLDER}/{body.params['public_id']}"
    rows, _ = await adb.cypher_query(
        """MATCH (u:User {uid:$uid})
        SET u.avatar_signature_lock=coalesce(u.avatar_signature_lock,0)+1
        WITH u OPTIONAL MATCH (j:ImageCleanup)
        WHERE j.public_id STARTS WITH $prefix
            AND j.cloud=$cloud
            AND j.created_at>datetime()-duration({hours:1})
        RETURN count(j), count(CASE WHEN j.public_id=$id THEN 1 END)""",
        params={
            "uid": user.id.hex,
            "cloud": settings.CLOUDINARY_CLOUD_NAME,
            "prefix": f"{settings.CLOUDINARY_AVATAR_FOLDER}/{user.id}/",
            "id": public_id,
        },
    )
    if rows and rows[0][0] >= MAX_UPLOADS_PER_HOUR and not rows[0][1]:
        raise HTTPException(
            429,
            "画像のアップロードが多すぎます。時間をおいてください。",
        )
    # Successful uploads are protected by the User reference; abandoned ones expire.
    await schedule_delete(public_id, delay=GRACE_SECONDS)
    return {"signature": signature}


@router.delete("/user/avatar")
async def delete_avatar(user: ActiveUser) -> dict:
    """Remove the DB reference now; remote deletion is retried in background."""
    await schedule_avatar_delete(user.avatar_url)
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.avatar_url=''",
        params={"uid": user.id.hex},
    )
    return {"avatar_url": "", "cleanup": "scheduled"}


@router.get("/admin/images")
async def list_images(
    _admin: AdminUser,
    cursor: Annotated[str | None, Query(max_length=1000)] = None,
) -> dict:
    """Paginate provider inventory, no background full-library scans."""
    settings = Settings()
    params = {"prefix": settings.CLOUDINARY_AVATAR_FOLDER + "/", "max_results": 100}
    if cursor:
        params["next_cursor"] = cursor
    result = await CloudinaryClient(settings).request(
        "GET",
        "resources/image/upload",
        params=params,
    )
    refs = await referenced_ids()
    resources = [
        {
            "public_id": item["public_id"],
            "bytes": item.get("bytes", 0),
            "created_at": item.get("created_at"),
            "url": item.get("secure_url"),
            "referenced": item["public_id"] in refs,
            "can_delete": avatar_folder_id(item["public_id"], settings)
            and item["public_id"] not in refs,
        }
        for item in result.get("resources", [])
    ]
    rows, _ = await adb.cypher_query(
        """MATCH (j:ImageCleanup)
        RETURN count(j), count(CASE WHEN j.attempts>0 THEN 1 END)""",
    )
    return {
        "resources": resources,
        "next_cursor": result.get("next_cursor"),
        "pending": rows[0][0],
        "retrying": rows[0][1],
    }


@router.post("/admin/images/cleanup")
async def cleanup_images(body: CleanupRequest, _admin: AdminUser) -> dict:
    """Recheck references and provider age, do not trust stale UI selection."""
    settings = Settings()
    refs = await referenced_ids()
    client = CloudinaryClient(settings)
    accepted = []
    for public_id in dict.fromkeys(body.public_ids):
        if not managed_id(public_id, settings) or public_id in refs:
            continue
        # Cloudinary path IDs must be URL encoded, including slashes.
        resource = await client.request(
            "GET",
            "resources/image/upload/" + quote(public_id, safe=""),
        )
        if eligible(resource, refs, settings):
            await schedule_delete(public_id)
            accepted.append(public_id)
    return {"scheduled": accepted}


@router.post("/admin/images/delete")
async def delete_images(body: CleanupRequest, _admin: AdminUser) -> dict:
    """Explicit manual deletion bypasses age/name rules, never live references."""
    settings = Settings()
    client = CloudinaryClient(settings)
    deleted, skipped = [], []
    for public_id in dict.fromkeys(body.public_ids):
        if not avatar_folder_id(public_id, settings):
            skipped.append(public_id)
            continue
        if public_id in await referenced_ids():
            skipped.append(public_id)
            continue
        await client.destroy(public_id, manual=True)
        deleted.append(public_id)
    return {"deleted": deleted, "skipped": skipped}
