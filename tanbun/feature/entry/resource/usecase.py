"""usecase."""

import asyncio
import re
from dataclasses import dataclass
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
    fetch_resource_by_key,
    fetch_resources_by_user,
    save_or_move_resource,
)
from tanbun.feature.entry.resource.repo.diff_update.preview import ResourceDiffPreview
from tanbun.feature.entry.resource.repo.diff_update.repo import (
    ResourceDiffPlan,
    prepare_resource_diff,
    update_resource_diff,
)
from tanbun.feature.entry.resource.repo.repair import rebuild_resource_structure
from tanbun.feature.entry.resource.repo.restore import restore_sysnet
from tanbun.feature.entry.resource.repo.save import (
    has_complete_persisted_structure,
    repair_persisted_root,
    sn2db,
)
from tanbun.feature.parsing.domain import try_parse2net
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import KNode

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


async def _structure_missing(
    ns: NameSpace,
    resource: MResource | None,
    txt: str,
) -> bool:
    """本文統計があるのにResourceから本文の一部または全部が切れているか."""
    if resource is None:
        return False
    stats = ns.stats.get(resource.uid.hex)
    return (
        stats is not None
        and stats.n_sentence > 0
        and not await has_complete_persisted_structure(
            resource.uid,
            len(re.findall(r"^#{2,6}(?:\s|$)", txt, flags=re.MULTILINE)),
        )
    )


@dataclass(frozen=True)
class _DetailChange:
    """本文の保存に必要な変更状態."""

    content_changed: bool
    cache_missing: bool
    structure_missing: bool
    network: SysNet | None
    plan: ResourceDiffPlan | None
    structure_state: tuple[SysNet, dict[KNode, UUID]] | None

    @property
    def needed(self) -> bool:
        """本文またはその付随データの更新が必要か."""
        return self.content_changed or self.cache_missing or self.structure_missing


async def _prepare_detail_change(
    ns: NameSpace,
    existing: MResource | None,
    meta: ResourceMeta,
    txt: str,
    *,
    identity_resolutions: dict[str, str | None] | None,
    term_identity_resolutions: dict[str, str | None] | None,
) -> _DetailChange:
    """本文の差分更新または構造修復を準備."""
    content_changed = existing is None or existing.txt_hash != meta.txt_hash
    cache_missing = existing is not None and existing.uid.hex not in ns.stats
    structure_missing = await _structure_missing(ns, existing, txt)
    needed = content_changed or cache_missing or structure_missing
    network = try_parse2net(txt) if needed else None
    plan: ResourceDiffPlan | None = None
    structure_state: tuple[SysNet, dict[KNode, UUID]] | None = None
    if existing is not None and needed:
        if structure_missing:
            await repair_persisted_root(existing.uid)
            if not content_changed and not cache_missing:
                structure_state = await restore_sysnet(existing.uid)
        if structure_state is None and network is not None:
            plan = await prepare_resource_diff(
                existing.uid,
                network,
                identity_resolutions,
                term_identity_resolutions,
            )
    return _DetailChange(
        content_changed,
        cache_missing,
        structure_missing,
        network,
        plan,
        structure_state,
    )


async def _persist_detail_change(
    *,
    cache_exists: bool,
    existing: MResource | None,
    resource_id: UUIDy,
    txt: str,
    change: _DetailChange,
    do_print: bool,
    identity_resolutions: dict[str, str | None] | None,
    term_identity_resolutions: dict[str, str | None] | None,
) -> SysNet | None:
    """準備した本文の変更をDBへ保存."""
    network = change.network
    if not cache_exists:
        network = network or try_parse2net(txt)
        if existing is None:
            await sn2db(network, resource_id, do_print)
        else:
            await update_resource_diff(
                resource_id,
                network,
                identity_resolutions=identity_resolutions,
                term_identity_resolutions=term_identity_resolutions,
                plan=change.plan,
            )
        return network
    if change.structure_state is not None:
        network = network or try_parse2net(txt)
        current, current_uids = change.structure_state
        await rebuild_resource_structure(resource_id, current, current_uids, network)
        return network
    if change.content_changed or change.structure_missing:
        network = network or try_parse2net(txt)
        await update_resource_diff(
            resource_id,
            network,
            identity_resolutions=identity_resolutions,
            term_identity_resolutions=term_identity_resolutions,
            plan=change.plan,
        )
        return network
    return None


async def preview_resource_update(
    ns: NameSpace,
    txt: str,
    path: list[str] | None = None,
    identity_resolutions: dict[str, str | None] | None = None,
    term_identity_resolutions: dict[str, str | None] | None = None,
) -> ResourceDiffPreview:
    """DBを変更せず、Resource保存で適用される差分を返す."""
    meta = ResourceMeta.from_str(txt, path)
    existing = ns.get_resource_or_none(meta.title) or await fetch_resource_by_key(
        ns.user_id,
        meta.title,
    )
    cache_missing = existing is not None and existing.uid.hex not in ns.stats
    structure_missing = await _structure_missing(ns, existing, txt)
    if (
        existing is not None
        and existing.txt_hash == meta.txt_hash
        and not cache_missing
        and not structure_missing
    ):
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
    existing = ns.get_resource_or_none(meta.title) or await fetch_resource_by_key(
        ns.user_id,
        meta.title,
    )
    change = await _prepare_detail_change(
        ns,
        existing,
        meta,
        txt,
        identity_resolutions=identity_resolutions,
        term_identity_resolutions=term_identity_resolutions,
    )
    same_location = existing is not None and (
        ns.roots_.get(meta.title) == existing
        if meta.path is None
        else ns.get_or_none(*meta.names) == existing
    )
    resource_unchanged = (
        existing is not None and not change.content_changed and same_location
    )
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
    saved_network = await _persist_detail_change(
        cache_exists=cache is not None,
        existing=existing,
        resource_id=lb.uid,
        txt=txt,
        change=change,
        do_print=do_print,
        identity_resolutions=identity_resolutions,
        term_identity_resolutions=term_identity_resolutions,
    )
    if saved_network is not None:
        await save_resource_stats_cache(dbmeta.uid, saved_network)

    changed = not resource_unchanged or change.cache_missing or change.structure_missing
    return dbmeta, meta, changed


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
