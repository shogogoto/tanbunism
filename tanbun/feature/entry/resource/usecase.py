"""usecase."""

from datetime import datetime
from uuid import UUID

from tanbun.feature.domain.errors import NotFoundError
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.entry.domain import NameSpace, ResourceMeta
from tanbun.feature.entry.errors import ResourceSaveOptimisticLockError
from tanbun.feature.entry.label import LResource
from tanbun.feature.entry.mapper import MResource
from tanbun.feature.entry.namespace import (
    fetch_namespace,
    fetch_resources_by_user,
    save_or_move_resource,
)
from tanbun.feature.entry.resource.repo.diff_update.repo import update_resource_diff
from tanbun.feature.entry.resource.repo.save import sn2db
from tanbun.feature.parsing.domain import try_parse2net
from tanbun.feature.parsing.sysnet import SysNet

from .stats.repo import save_resource_stats_cache


async def _check_duplication(user_id: UUID, title: str):
    rs = await fetch_resources_by_user(user_id)
    rs = [r for r in rs if r.name == title]
    if len(rs) > 1:
        msg = f"同一リソース'{title}を重複して新規作成しました"
        raise ResourceSaveOptimisticLockError(msg)


async def save_resource_with_detail(
    ns: NameSpace,
    txt: str,
    path: list[str] | None = None,
    updated: datetime | None = None,
    do_print: bool = False,  # noqa: FBT001, FBT002
) -> tuple[MResource, ResourceMeta]:
    """テキストからResource内のTanbunネットワークを永続化."""
    meta = ResourceMeta.from_str(txt, path, updated)
    existing = ns.get_resource_or_none(meta.title)
    content_changed = existing is None or existing.txt_hash != meta.txt_hash
    cache_missing = existing is not None and existing.uid.hex not in ns.stats
    sn = try_parse2net(txt) if content_changed or cache_missing else None
    lb = await save_or_move_resource(meta, ns)
    await _check_duplication(ns.user_id, meta.title)
    r = await LResource.nodes.get(uid=lb.uid)
    if updated is not None and r.updated != updated:
        msg = f"'{meta.title}'は同時に更新されたのでロールバック"
        raise ResourceSaveOptimisticLockError(msg)
    if lb is None:
        msg = f"{meta.title} の保存に失敗しました"
        raise NotFoundError(msg)
    # ２重リソース不整合がないことを最終確認
    await fetch_namespace(ns.user_id)

    dbmeta = MResource.freeze_dict(lb.__properties__)
    cache = await r.cached_stats.get_or_none()
    if cache is None:  # 新規作成
        if sn is None:
            sn = try_parse2net(txt)
        await sn2db(sn, lb.uid, do_print)
        await save_resource_stats_cache(dbmeta.uid, sn)
    if cache is not None and content_changed:  # 差分更新
        if sn is None:
            sn = try_parse2net(txt)
        await update_resource_diff(lb.uid, sn)
        await save_resource_stats_cache(dbmeta.uid, sn)

    return dbmeta, meta


async def save_text(
    user_id: UUIDy,
    s: str,
    path: tuple[str, ...] | None = None,
    updated: datetime | None = None,
    do_print: bool = False,  # noqa: FBT001, FBT002
) -> tuple[SysNet, MResource]:
    """テキストをリソース詳細として保存するラッパー."""
    ns = await fetch_namespace(to_uuid(user_id))
    m, _meta = await save_resource_with_detail(
        ns,
        s,
        list(path) if path is not None else None,
        updated,
        do_print,
    )
    sn = try_parse2net(s)
    return sn, m
