"""usecase."""

import asyncio
from datetime import datetime
from uuid import UUID
from weakref import WeakValueDictionary

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
from tanbun.feature.entry.resource.repo.diff_update.preview import ResourceDiffPreview
from tanbun.feature.entry.resource.repo.diff_update.repo import (
    ResourceDiffPlan,
    prepare_resource_diff,
    update_resource_diff,
)
from tanbun.feature.entry.resource.repo.save import sn2db
from tanbun.feature.parsing.domain import try_parse2net
from tanbun.feature.parsing.sysnet import SysNet

from .stats.repo import save_resource_stats_cache

_resource_save_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _resource_save_lock(user_id: UUID, title: str) -> asyncio.Lock:
    """同一ユーザー・タイトルの保存に使うプロセス内ロック."""
    key = f"{user_id.hex}:{title}"
    lock = _resource_save_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _resource_save_locks[key] = lock
    return lock


async def preview_resource_update(
    ns: NameSpace,
    txt: str,
    path: list[str] | None = None,
    identity_resolutions: dict[str, str | None] | None = None,
    term_identity_resolutions: dict[str, str | None] | None = None,
) -> ResourceDiffPreview:
    """DBを変更せず、Resource保存で適用される差分を返す."""
    meta = ResourceMeta.from_str(txt, path)
    existing = ns.get_resource_or_none(meta.title)
    if existing is not None and existing.txt_hash == meta.txt_hash:
        return ResourceDiffPreview.unchanged(existing.uid)
    network = try_parse2net(txt)
    if existing is None:
        return ResourceDiffPreview.for_new(network)
    plan = await prepare_resource_diff(
        existing.uid,
        network,
        identity_resolutions,
        term_identity_resolutions,
    )
    return ResourceDiffPreview.from_plan(existing.uid, plan)


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
    *,
    identity_resolutions: dict[str, str | None] | None = None,
    term_identity_resolutions: dict[str, str | None] | None = None,
) -> tuple[MResource, ResourceMeta, bool]:
    """テキストからResource内のTanbunネットワークを永続化."""
    meta = ResourceMeta.from_str(txt, path, updated)
    lock = _resource_save_lock(ns.user_id, meta.title)
    if lock.locked():
        msg = f"'{meta.title}'は同時に保存されています"
        raise ResourceSaveOptimisticLockError(msg)
    async with lock:
        return await _save_resource_with_detail(
            ns,
            txt,
            meta,
            updated,
            do_print,
            identity_resolutions=identity_resolutions,
            term_identity_resolutions=term_identity_resolutions,
        )


async def _save_resource_with_detail(
    ns: NameSpace,
    txt: str,
    meta: ResourceMeta,
    updated: datetime | None,
    do_print: bool,  # noqa: FBT001
    *,
    identity_resolutions: dict[str, str | None] | None,
    term_identity_resolutions: dict[str, str | None] | None,
) -> tuple[MResource, ResourceMeta, bool]:
    """競合確認後にResourceを永続化する."""
    existing = ns.get_resource_or_none(meta.title)
    content_changed = existing is None or existing.txt_hash != meta.txt_hash
    cache_missing = existing is not None and existing.uid.hex not in ns.stats
    sn = try_parse2net(txt) if content_changed or cache_missing else None
    diff_plan: ResourceDiffPlan | None = None
    if existing is not None and content_changed and not cache_missing:
        if sn is None:
            sn = try_parse2net(txt)
        diff_plan = await prepare_resource_diff(
            existing.uid,
            sn,
            identity_resolutions,
            term_identity_resolutions,
        )
    same_location = existing is not None and (
        ns.roots_.get(meta.title) == existing
        if meta.path is None
        else ns.get_or_none(*meta.names) == existing
    )
    resource_unchanged = existing is not None and not content_changed and same_location
    lb = (
        await LResource.nodes.get(uid=existing.uid.hex)
        if resource_unchanged
        else await save_or_move_resource(meta, ns)
    )
    await _check_duplication(ns.user_id, meta.title)
    r = await LResource.nodes.get(uid=lb.uid)
    if not resource_unchanged and updated is not None and r.updated != updated:
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
        await update_resource_diff(
            lb.uid,
            sn,
            identity_resolutions=identity_resolutions,
            term_identity_resolutions=term_identity_resolutions,
            plan=diff_plan,
        )
        await save_resource_stats_cache(dbmeta.uid, sn)

    return dbmeta, meta, not resource_unchanged


async def save_text(
    user_id: UUIDy,
    s: str,
    path: tuple[str, ...] | None = None,
    updated: datetime | None = None,
    do_print: bool = False,  # noqa: FBT001, FBT002
) -> tuple[SysNet, MResource]:
    """テキストをリソース詳細として保存するラッパー."""
    ns = await fetch_namespace(to_uuid(user_id))
    m, _meta, _changed = await save_resource_with_detail(
        ns,
        s,
        list(path) if path is not None else None,
        updated,
        do_print,
    )
    sn = try_parse2net(s)
    return sn, m
