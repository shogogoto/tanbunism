"""Single boundary for Cloudinary credentials, identities and API calls."""

import hashlib
import re
import time
from urllib.parse import unquote, urlparse
from uuid import UUID

import httpx
from fastapi import HTTPException

from tanbun.config.env import Settings

SIGNATURE_CLOCK_SKEW = 300
UPLOAD_ID_PARTS = 2


def configured(settings: Settings) -> bool:
    """Check whether deletion credentials are configured."""
    return bool(
        settings.CLOUDINARY_CLOUD_NAME
        and settings.CLOUDINARY_API_KEY
        and settings.CLOUDINARY_API_SECRET,
    )


def managed_id(public_id: str, settings: Settings) -> bool:
    """Only UUID-named avatars in our dedicated prefix are managed."""
    prefix = settings.CLOUDINARY_AVATAR_FOLDER + "/"
    if not public_id.startswith(prefix):
        return False
    parts = public_id[len(prefix) :].split("/")
    if len(parts) not in {1, 2}:
        return False
    try:
        for part in parts:
            UUID(part)
    except ValueError:
        return False
    return True


def avatar_folder_id(public_id: str, settings: Settings) -> bool:
    """Manual admin deletion is scoped to the avatar folder, not UUID names."""
    prefix = settings.CLOUDINARY_AVATAR_FOLDER + "/"
    return public_id.startswith(prefix) and bool(public_id[len(prefix) :])


def avatar_id(
    url: str | None,
    settings: Settings,
    *,
    managed_only: bool = True,
) -> str | None:
    """Preserve old crop/version URLs; never treat external avatars as ours."""
    if not url or not settings.CLOUDINARY_CLOUD_NAME:
        return None
    parsed = urlparse(url)
    prefix = f"/{settings.CLOUDINARY_CLOUD_NAME}/image/upload/"
    if parsed.hostname != "res.cloudinary.com" or not parsed.path.startswith(prefix):
        return None
    path = unquote(parsed.path[len(prefix) :])
    # Public IDs have a known prefix, even for legacy URLs without a version.
    segments = path.split("/")
    folder = settings.CLOUDINARY_AVATAR_FOLDER.split("/")
    for position in range(len(segments)):
        if segments[position : position + len(folder)] == folder:
            candidate = "/".join(segments[position:]).rsplit(".", 1)[0]
            valid = managed_id if managed_only else avatar_folder_id
            return candidate if valid(candidate, settings) else None
    return None


def sign(params: dict[str, str], secret: str) -> str:
    """Cloudinary sorted name=value SHA-256 signature (not URL encoded)."""
    payload = "&".join(
        f"{key}={value}" for key, value in sorted(params.items()) if value
    )
    return hashlib.sha256((payload + secret).encode()).hexdigest()


def upload_signature(params: dict[str, str], user_id: UUID, settings: Settings) -> str:
    """Do not provide a general-purpose signing oracle."""
    if not configured(settings) or not settings.CLOUDINARY_UPLOAD_PRESET:
        raise HTTPException(
            503,
            "Cloudinaryの認証情報・署名付きpresetを設定してください。",
        )
    allowed = {
        "timestamp",
        "public_id",
        "folder",
        "upload_preset",
        "source",
        "custom_coordinates",
        "overwrite",
        "invalidate",
    }
    try:
        public_id = params["public_id"].split("/")
        valid = (
            len(public_id) == UPLOAD_ID_PARTS
            and UUID(public_id[0]) == user_id
            and public_id[0] == str(user_id)
            and bool(UUID(public_id[1]))
            and abs(int(params["timestamp"]) - time.time()) <= SIGNATURE_CLOCK_SKEW
            and params["folder"] == settings.CLOUDINARY_AVATAR_FOLDER
            and params["upload_preset"] == settings.CLOUDINARY_UPLOAD_PRESET
            and params.get("overwrite") == "false"
            and params.get("invalidate", "true") == "true"
            and params.get("source", "uw") == "uw"
            and not (params.keys() - allowed)
        )
    except (KeyError, ValueError):
        valid = False
    coordinates = params.get("custom_coordinates")
    if coordinates and not re.fullmatch(r"\d+,\d+,[1-9]\d*,[1-9]\d*", coordinates):
        valid = False
    if not valid:
        raise HTTPException(400, "本人のアバター用アップロードのみ署名できます。")
    return sign(params, settings.CLOUDINARY_API_SECRET)


class CloudinaryClient:
    """Bounded HTTP calls; never disclose provider errors or secrets to clients."""

    def __init__(self, settings: Settings):
        """Use the configured cloud for all requests."""
        self.settings = settings

    async def request(self, method: str, path: str, **kwargs) -> dict:
        """Use Basic Auth for server-side calls."""
        s = self.settings
        if not configured(s):
            raise HTTPException(503, "Cloudinaryの認証情報を設定してください。")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.request(
                    method,
                    f"https://api.cloudinary.com/v1_1/{s.CLOUDINARY_CLOUD_NAME}/{path}",
                    auth=(s.CLOUDINARY_API_KEY, s.CLOUDINARY_API_SECRET),
                    **kwargs,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            explanations = {
                401: (
                    "認証に失敗しました。RenderのAPI Key・API Secretと"
                    "Cloud Nameの組み合わせを確認してください。"
                ),
                403: (
                    "アクセスが拒否されました。"
                    "CloudinaryのAPIキーの権限を確認してください。"
                ),
                404: "対象が見つかりません。Cloud Nameと対象画像を確認してください。",
                420: "APIの利用上限に達しました。時間を置いて再試行してください。",
                429: "APIの利用上限に達しました。時間を置いて再試行してください。",
            }
            explanation = explanations.get(
                code,
                "要求が失敗しました。設定を確認し、再試行してください。",
            )
            raise HTTPException(
                502,
                f"Cloudinary (HTTP {code}): {explanation}",
            ) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(
                504,
                "Cloudinaryへの接続がタイムアウトしました。再試行してください。",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                502,
                "Cloudinaryとの通信に失敗しました。再試行してください。",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                502,
                "Cloudinaryからの応答を読み取れませんでした。再試行してください。",
            ) from exc

    async def destroy(self, public_id: str, *, manual: bool = False) -> None:
        """Only app-owned images; delete originals/derivatives and invalidate CDN."""
        valid = avatar_folder_id if manual else managed_id
        if not valid(public_id, self.settings):
            msg = "Unmanaged image"
            raise ValueError(msg)
        result = await self.request(
            "POST",
            "image/destroy",
            data={
                "public_id": public_id,
                "invalidate": "true",
            },
        )
        if result.get("result") not in {"ok", "not found"}:
            msg = "Image deletion was not acknowledged"
            raise RuntimeError(msg)
