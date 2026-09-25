"""routers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

import chardet  # 文字エンコーディング検出用
from fastapi import APIRouter, Body, UploadFile

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.entry.domain import NameSpace, ResourceDetail, ResourceSearchResult
from tanbun.feature.entry.errors import NotOwnerError
from tanbun.feature.entry.label import LResource
from tanbun.feature.entry.namespace import (
    ResourceMetas,
    delete_folder,
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
from tanbun.feature.entry.resource.repo.owner import check_entry_owner
from tanbun.feature.entry.resource.repo.restore import restore_graph
from tanbun.feature.entry.resource.repo.search import search_resources
from tanbun.feature.entry.resource.usecase import save_resource_with_detail
from tanbun.feature.entry.router.param import ResourceSearchBody
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


@router.post("/resource-text")
async def post_text(
    txt: Annotated[str, Body(embed=True)],
    path: Annotated[list[str], Body(embed=True)],
    user: ActiveUser,
) -> dict[str, str]:
    """テキストからsysnetを読み取って永続化."""
    validate_resource_text(txt)
    ns = await fetch_namespace(user.id)
    m, _ = await save_resource_with_detail(ns, txt, path)
    return {"resource_id": m.uid.hex}


@router.post("/resource")
async def post_files(
    files: list[UploadFile],
    user: ActiveUser,
) -> None:
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

    for file, text in resources:
        ns = await fetch_namespace(user.id)
        await save_resource_with_detail(
            ns,
            text,
            path=file.filename.split("/") if file.filename else None,
            updated=datetime.now(tz=TZ),
        )


@router.get("/resource/{resource_id}")
async def get_resource_detail(resource_id: str) -> ResourceDetail:
    """リソース詳細."""
    g, uids, terms = await restore_graph(resource_id)
    info = await fetch_info_by_resource_uid(resource_id)
    return ResourceDetail(g=g, resource_info=info, uids=uids, terms=terms)


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
