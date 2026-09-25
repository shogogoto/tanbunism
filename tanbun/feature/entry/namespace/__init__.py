"""リソースの詳細(単文)を含まないリソースのメタ情報repo."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path, PurePath
from uuid import UUID

import neo4j
import networkx as nx
from more_itertools import collapse
from neomodel.async_.core import AsyncDatabase
from neomodel.exceptions import ConstraintValidationFailed, UniqueProperty
from pydantic import RootModel
from pydantic_core import Url

from tanbun.feature.domain.errors import NotFoundError
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.entry.domain import NameSpace, ResourceInfo, ResourceMeta
from tanbun.feature.entry.errors import (
    DuplicatedTitleError,
    EntryAlreadyExistsError,
    FolderDeleteError,
    ResourceSaveOptimisticLockError,
    SaveResourceError,
)
from tanbun.feature.entry.label import LFolder, LResource, LResourceStatsCache
from tanbun.feature.entry.mapper import MFolder, MResource
from tanbun.feature.entry.resource.stats.domain import ResourceStats
from tanbun.feature.user.label import LUser
from tanbun.feature.user.public_schema import UserReadPublic


class ResourceMetas(RootModel[list[ResourceMeta]]):
    """リクエスト用."""

    def check_duplicated_title(self) -> None:
        """titleの重複を許さない."""
        titles = [m.title for m in self.root]
        if len(titles) != len(set(titles)):
            msg = "titleが重複しています"
            raise DuplicatedTitleError(msg)


async def fetch_namespace(user_id: UUIDy) -> NameSpace:
    """ユーザー配下のサブフォルダ."""
    q = """
        MATCH (user:User {uid: $uid})
            , p = (user)<-[:OWNED|PARENT]-*(e:Entry)
        OPTIONAL MATCH (e)-[:STATS]->(stat:ResourceStatsCache)
        RETURN p, stat, e
    """
    uid = to_uuid(user_id)
    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={"uid": uid.hex},
        resolve_objects=True,
    )

    g = nx.DiGraph()
    ns = NameSpace(roots_={}, g=g, user_id=uid)
    stats = {}
    for row in rows:
        path: neo4j.graph.Path = row[0]
        stat: LResourceStatsCache | None = row[1]
        e = row[2]
        if stat is not None:
            stats[e.uid] = ResourceStats.model_validate(stat.__properties__)
        for e1, e2 in pairwise(path.nodes):
            if isinstance(e1, LUser):
                ns.add_root(e2.frozen)
            else:
                ns.add_edge(e1.frozen, e2.frozen)
    ns.stats = stats
    return ns


async def fill_parents(ns: NameSpace, *names: str) -> LFolder | None:
    """フォルダをDB上で繋げてtailを返す."""
    if len(names) == 0:
        return None
    path = ns.get_path(*names)
    tail = next((p for p in reversed(path) if p is not None), None)
    match tail:
        case None:  # 新規作成 = tail なし = 既存親なし
            u: LUser = await LUser.nodes.get(uid=ns.user_id.hex)
            folders = await LFolder.create(*[{"name": name} for name in names])
            head: LFolder = folders[0]
            await head.owner.connect(u)
            ns.add_root(head.frozen)
            for f1, f2 in pairwise(folders):
                await f2.parent.connect(f1)
                ns.add_edge(f1.frozen, f2.frozen)
            return folders[-1]
        case MFolder():  # フォルダが途中まで既存
            i = path.index(tail) + 1  # not none の最初の位置
            folders = await LFolder.create(*[{"name": name} for name in names[i:]])
            folders = [LFolder(**tail.model_dump()), *folders]
            for f1, f2 in pairwise(folders):
                await f2.parent.connect(f1)
                ns.g.add_edge(f1.frozen, f2.frozen)
            return folders[-1]
        case _:
            raise ValueError


async def _save_unique_resource(
    resource: LResource,
    ns: NameSpace,
    title: str,
) -> LResource:
    """ユーザー内の同名Resource作成をDB制約でも防ぐ."""
    resource.resource_key = f"{ns.user_id.hex}:{title}"
    try:
        return await resource.save()
    except (ConstraintValidationFailed, UniqueProperty) as error:
        msg = f"'{title}'は既に登録済みです"
        raise ResourceSaveOptimisticLockError(msg) from error


async def save_resource(m: ResourceMeta, ns: NameSpace) -> LResource | None:
    """新規作成 or 更新して返す."""
    path = ns.get_path(*m.names)
    tail = next((p for p in reversed(path) if p is not None), None)
    match tail:
        case None | MFolder():  # 新規作成 or フォルダが途中まで既存
            lb = await _save_unique_resource(
                LResource(**m.model_dump()),
                ns,
                m.title,
            )
            parent = await fill_parents(ns, *m.names[:-1])
            if parent is None:  # user直下
                u = await LUser.nodes.get(uid=ns.user_id.hex)
                await lb.owner.connect(u)
                ns.add_root(lb.frozen)
            else:
                await lb.parent.connect(parent)
                ns.add_edge(parent.frozen, lb.frozen)
            return lb
        case MResource():  # 更新
            if m.txt_hash == tail.txt_hash:  # 変更なし
                return None
            lb = LResource(**tail.model_dump())
            for k, v in m.model_dump().items():
                setattr(lb, k, v)
            lb.updated = datetime.now()  # noqa: DTZ005
            return await _save_unique_resource(lb, ns, m.title)
        case _:
            raise ValueError


async def save_or_move_resource(
    m: ResourceMeta,
    ns: NameSpace,
) -> LResource:
    """移動を反映してsave."""
    # NSに重複したタイトルがあると困る

    old = ns.get_resource_or_none(m.title)
    if old is None:  # 新規
        return await save_resource(m, ns)
    ns.remove_resource(m.title)

    d = old.model_dump()
    d.update(m.model_dump())
    d["uid"] = d["uid"].hex  # ハイフンありに変換されると単文との結びつきがなくなる
    upd = await _save_unique_resource(LResource(**d), ns, m.title)  # reflesh
    owner = await upd.owner.get_or_none()
    parent = await upd.parent.get_or_none()
    if owner is None and parent is None:
        msg = "所有者も親もなかったなんてあり得ないからね"
        raise SaveResourceError(msg)

    # 既存の繋がりを切る
    if parent is None:  # owner直下
        await upd.owner.disconnect(owner)
    else:
        await upd.parent.disconnect(parent)

    new_parent = await fill_parents(ns, *m.names[:-1])
    if new_parent is None:  # user直下
        u = await LUser.nodes.get(uid=ns.user_id.hex)
        await upd.owner.connect(u)
        ns.add_root(upd.frozen)
    else:
        await upd.parent.connect(new_parent)
        ns.add_edge(new_parent.frozen, upd.frozen)
    return upd


async def sync_namespace(metas: ResourceMetas, ns: NameSpace) -> list[Path]:
    """変更や移動されたファイルパスをDBに反映して変更があったものを返す."""
    metas.check_duplicated_title()
    ls = []
    for m in metas.root:
        e = ns.get_or_none(*m.names)
        if e is None:
            lb = await save_or_move_resource(m, ns)
        else:
            lb = await save_resource(m, ns)
        if lb is not None:
            ls.append(Path().joinpath(*m.path))
    return ls


async def create_folder(user_id: UUIDy, *names: str) -> LFolder:
    """一般化フォルダ作成."""
    ns = await fetch_namespace(user_id)
    return await fill_parents(ns, *names)


async def create_resource(
    user_id: UUIDy,
    *names: str,
    authors: list[str] | None = None,
    published: date | None = None,
    urls: list[Url] | None = None,
) -> LResource:
    """リソース作成のfacade."""
    if len(names) == 0:
        msg = "entry名を1つ以上指定して"
        raise ValueError(msg)

    m = ResourceMeta(
        title=names[-1],
        authors=authors or [],
        published=published,
        urls=urls or [],
        path=tuple(names),
    )

    ns = await fetch_namespace(user_id)
    r = await save_resource(m, ns)
    if r is None:
        msg = f"{names[-1]} は既に存在します"
        raise EntryAlreadyExistsError(msg)
    return r


async def move_folder(
    user_id: UUIDy,
    target: PurePath | str,
    to: PurePath | str,
) -> LFolder:
    """フォルダの移動(配下ごと)."""
    target = PurePath(target)  # PathはOSのファイルシステムを参照するらしく不適
    to = PurePath(to)
    if not (target.is_absolute() and to.is_absolute()):
        msg = "絶対パスで指定して"
        raise ValueError(msg, target, to)
    fs = await fetch_namespace(user_id)
    tgt = LFolder(**fs.get(*target.parts[1:]).model_dump())
    p = await tgt.parent.get()
    await tgt.parent.disconnect(p)
    to_names = to.parts[1:]
    if len(to_names) == 0:  # rootへ
        luser = await LUser.nodes.get(uid=user_id)
        await tgt.owner.connect(luser)
        return tgt
    parent_to_move = LFolder(**fs.get(*to_names[:-1]).model_dump())
    await tgt.parent.connect(parent_to_move)
    tgt.name = to_names[-1]
    return await tgt.save()


async def resource_infos_by_resource_uids(
    resource_uids: Iterable[UUID],
) -> dict[UUID, ResourceInfo]:
    """resource_uidの各々のリソースの所有者を返す."""
    q = """
        UNWIND $uids as uid
        MATCH p = (user:User)<-[:OWNED|PARENT]-*(r:Resource {uid: uid})
        MATCH (r)-[:STATS]->(stat:ResourceStatsCache)
        RETURN DISTINCT p, stat
        """
    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={"uids": list({uid.hex for uid in resource_uids})},
    )
    d = {}
    for row in rows:
        path: neo4j.graph.Path = row[0]
        stats = ResourceStats.model_validate(row[1])
        resource_path = [p.get("name") for p in path.nodes[1:]]
        r = MResource.freeze_dict(path.end_node)
        d[r.uid] = ResourceInfo(
            user=UserReadPublic.model_validate(path.start_node),
            resource=r.model_copy(
                update={"path": tuple(e for e in resource_path if e is not None)},
            ),
            resource_stats=stats,
        )
    return d


async def fetch_info_by_resource_uid(resource_uid: UUIDy) -> ResourceInfo:
    """Wrap tool for resource info."""
    uid = to_uuid(resource_uid)
    infos = await resource_infos_by_resource_uids([uid])
    return infos[uid]


async def delete_folder(folder_uid: UUIDy):
    """フォルダを削除."""
    f: LFolder | None = await LFolder.nodes.get_or_none(uid=to_uuid(folder_uid).hex)
    if f is None:
        msg = f"folder not found: {folder_uid}"
        raise NotFoundError(msg)
    children = await f.children.all()
    if len(children) > 0:
        msg = f"[{f.name}]を削除する前に子エントリを削除してください"
        raise FolderDeleteError(msg)
    await f.delete()


async def fetch_resources_by_user(user_id: UUIDy) -> list[LResource]:
    """同一ユーザーの全リソースを返す."""
    q = """
        MATCH (user:User {uid: $uid})
            , p = (user)<-[:OWNED|PARENT]-*(r:Resource)
        RETURN r
    """
    uid = to_uuid(user_id)
    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={"uid": uid.hex},
        resolve_objects=True,
    )

    return list(collapse(rows, base_type=LResource))
