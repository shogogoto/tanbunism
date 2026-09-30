"""routers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

import chardet  # 文字エンコーディング検出用
from fastapi import APIRouter, UploadFile

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.entry.domain import (
    EntryDetail,
    NameSpace,
    ResourceDetail,
    ResourceSearchResult,
)
from tanbun.feature.entry.errors import NotOwnerError
from tanbun.feature.entry.label import LResource
from tanbun.feature.entry.namespace import (
    ResourceMetas,
    delete_folder,
    fetch_entry_detail,
    fetch_info_by_resource_uid,
    fetch_namespace,
    sync_namespace,
)
from tanbun.feature.entry.resource.limits import (
    DEFAULT_RESOURCE_UPLOAD_LIMITS,
    validate_batch_size,
    validate_file_count,
    validate_file_size,
    validate_resource_text,
)
from tanbun.feature.entry.resource.repo.delete import delete_resource
from tanbun.feature.entry.resource.repo.diff_update.errors import (
    IdentityConflictResponse,
)
from tanbun.feature.entry.resource.repo.diff_update.preview import ResourceDiffPreview
from tanbun.feature.entry.resource.repo.owner import check_entry_owner
from tanbun.feature.entry.resource.repo.restore import restore_graph
from tanbun.feature.entry.resource.repo.search import search_resources
from tanbun.feature.entry.resource.usecase import (
    preview_resource_update,
    save_resource_with_detail,
)
from tanbun.feature.entry.router.param import ResourceSearchBody, ResourceTextBody
from tanbun.feature.user.router_util import ActiveUser, TrackUser

router = APIRouter(tags=["entry"])


def entry_router() -> APIRouter:  # noqa: D103
    return router


@router.post("/namespace")
async def sync_namespace_api(
    metas: ResourceMetas,
    user: ActiveUser,
) -> list[Path]:
    """ファイルシステムと同期."""
    ns = await fetch_namespace(user.id)
    return await sync_namespace(metas, ns)


@router.get("/namespace")
async def get_namaspace(
    user: ActiveUser,
) -> NameSpace:
    """ユーザーの名前空間."""
    return await fetch_namespace(user.id)


@router.get("/user/{user_id}/namespace")
async def get_public_namespace(user_id: UUID) -> NameSpace:
    """公開ユーザーのEntryとResourceを取得."""
    return await fetch_namespace(user_id)


@router.post(
    "/resource-text",
    responses={409: {"model": IdentityConflictResponse}},
)
async def post_text(
    body: ResourceTextBody,
    user: ActiveUser,
) -> dict[str, str]:
    """テキストからsysnetを読み取って永続化."""
    validate_resource_text(body.txt)
    ns = await fetch_namespace(user.id)
    m, _ = await save_resource_with_detail(
        ns,
        body.txt,
        body.path,
        identity_resolutions=body.resolution_map("sentence"),
        term_identity_resolutions=body.resolution_map("term"),
    )
    return {"resource_id": m.uid.hex}


@router.post(
    "/resource-text/preview",
    responses={409: {"model": IdentityConflictResponse}},
)
async def preview_text_update(
    body: ResourceTextBody,
    user: ActiveUser,
) -> ResourceDiffPreview:
    """保存せずにResourceの差分と同一性競合を検証する."""
    validate_resource_text(body.txt)
    ns = await fetch_namespace(user.id)
    return await preview_resource_update(
        ns,
        body.txt,
        body.path,
        body.resolution_map("sentence"),
        body.resolution_map("term"),
    )


@router.post("/resource")
async def post_files(
    files: list[UploadFile],
    user: ActiveUser,
) -> dict[str, list[str]]:
    """ファイルからsysnetを読み取って永続化."""
    validate_file_count(len(files))

    async def read_content(file: UploadFile) -> tuple[str, int]:
        """ファイルの内容を適切なエンコーディングで読み込む."""
        content = await file.read(
            DEFAULT_RESOURCE_UPLOAD_LIMITS.max_file_bytes + 1,
        )
        validate_file_size(len(content))
        encoding = chardet.detect(content)["encoding"] or "utf-8"
        return content.decode(encoding), len(content)

    resources: list[tuple[UploadFile, str]] = []
    total_size = 0
    for file in files:
        text, size = await read_content(file)
        total_size += size
        validate_batch_size(total_size)
        resources.append((file, text))

    resource_ids: list[str] = []
    for file, text in resources:
        ns = await fetch_namespace(user.id)
        resource, _ = await save_resource_with_detail(
            ns,
            text,
            path=file.filename.split("/") if file.filename else None,
            updated=datetime.now(tz=TZ),
        )
        resource_ids.append(resource.uid.hex)
    return {"resource_ids": resource_ids}


@router.get("/resource/{resource_id}")
async def get_resource_detail(resource_id: str) -> ResourceDetail:
    """リソース詳細."""
    g, uids, terms = await restore_graph(resource_id)
    info = await fetch_info_by_resource_uid(resource_id)
    return ResourceDetail(g=g, resource_info=info, uids=uids, terms=terms)


@router.get("/entry/{entry_id}")
async def get_entry_detail(entry_id: UUID) -> EntryDetail:
    """Entryの親階層と直下のEntryを取得."""
    return await fetch_entry_detail(entry_id)


# resourceの削除とfolderの削除を統合したい
# folderの削除は子にresourceがない場合にのみ可能
@router.delete("/entry/{entry_id}")
async def delete_entry_api(
    entry_id: UUID,
    user: ActiveUser,
) -> None:
    """リソース削除."""
    if not await check_entry_owner(user.uid, entry_id):
        msg = "リソースを削除できるのは所有者のみです"
        raise NotOwnerError(msg=msg)

    if await LResource.nodes.get_or_none(uid=entry_id.hex):
        await delete_resource(entry_id)
    else:
        await delete_folder(entry_id)


@router.post("/resource/search")
async def search_resource_post(
    body: ResourceSearchBody,
    user: TrackUser = None,
) -> ResourceSearchResult:
    """リソース検索(POST)."""
    return await search_resources(
        search_str=body.q,
        paging=body.paging,
        search_user=body.q_user,
        desc=body.desc,
        keys=body.order_by,
    )
