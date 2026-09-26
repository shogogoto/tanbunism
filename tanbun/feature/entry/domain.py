"""domain."""

from __future__ import annotations

from datetime import date, datetime
from hashlib import blake2b
from typing import Literal
from uuid import UUID

import networkx as nx
from pydantic import BaseModel, Field
from pydantic_core import Url

from tanbun.feature.domain.graph.schema import NXGraph
from tanbun.feature.entry.mapper import Entry, MFolder, MResource
from tanbun.feature.entry.resource.stats.domain import ResourceStats
from tanbun.feature.parsing.meta_parse import meta_parse, title_parse
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.time import parse2dt
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import KNode
from tanbun.feature.user.public_schema import UserReadPublic

from .errors import (
    EntryNotFoundError,
    ResourceSaveOptimisticLockError,
)


def resource_text_hash(text: str) -> int:
    """プロセスを再起動しても変わらない本文ハッシュを返す."""
    digest = blake2b(text.encode(), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big") & ((1 << 63) - 1)


class NameSpace(BaseModel):
    """リソースの分類."""

    g: NXGraph = Field(default_factory=nx.DiGraph)
    roots_: dict[str, Entry]
    user_id: UUID
    stats: dict[str, ResourceStats] = Field(default_factory=dict)

    def remove_resource(self, title: str) -> None:
        """リソースの削除."""
        r = self.get_resource_or_none(title)
        self.g.remove_node(r)
        if title in self.roots_:
            del self.roots_[title]

    def check_uniq_title(self, e: Entry) -> None:
        """titleの重複を許さない."""
        if isinstance(e, MResource):
            r = self.get_resource_or_none(e.name)
            if r is not None:
                msg = f"'{e.name}'は既に登録済み"
                raise ResourceSaveOptimisticLockError(msg)

    def add_root(self, e: Entry) -> None:
        """user直下."""
        self.check_uniq_title(e)
        self.roots_[e.name] = e
        self.g.add_node(e)

    def add_edge(self, parent: Entry, child: Entry) -> None:
        """user直下以外."""
        self.check_uniq_title(child)
        self.g.add_edge(parent, child)

    def children(self, root: str, *names: str) -> list[str]:
        """element_idなしで文字列だけでアクセス."""
        tgt = self.get_or_none(root, *names)
        if tgt is None:
            return []
        return sorted([s.name for s in self.g.successors(tgt)])

    @property
    def roots(self) -> list[str]:
        """ユーザー直下のフォルダ一覧."""
        return sorted(self.roots_.keys())

    def get_or_none(self, root: str, *names: str) -> Entry | None:
        """文字列でパス指定."""
        current = self.roots_.get(root, None)
        if current is None:
            return None
        for name in names:
            succs = [n for n in self.g.successors(current) if n.name == name]
            if len(succs) != 1:  # 存在しないパスに対して空を返す
                return None
            current = succs[0]
        return current

    def get_path(self, *names: str) -> list[Entry | None]:
        """対応するEntry、しないならNoneを返す."""
        n = len(names) + 1
        return [self.get_or_none(*names[:i]) for i in range(1, n)]

    def get(self, root: str, *names: str) -> Entry:
        """Noneの場合にエラー."""
        tgt = self.get_or_none(root, *names)
        if tgt is None:
            raise EntryNotFoundError
        return tgt

    @property
    def resources(self) -> list[MResource]:
        """resource一覧."""
        return [n for n in self.g if isinstance(n, MResource)]

    def get_resource_or_none(self, title: str) -> MResource | None:
        """titleでリソースを指定."""
        return next((r for r in self.resources if r.name == title), None)

    def get_resource(self, title: str) -> MResource:
        """titleでリソースを指定."""
        r = self.get_resource_or_none(title)
        if r is None:
            msg = f"{title}は登録されていません"
            raise ValueError(msg, title)
        return r


class ResourceMeta(BaseModel):
    """リソースメタ情報."""

    title: str
    authors: list[str] = Field(default_factory=list)
    published: date | None = None
    urls: list[Url] = Field(default_factory=list)

    # ファイル由来
    path: tuple[str, ...] | None = Field(default=None, min_length=1)
    # updated: datetime | None = None
    updated: datetime = Field(default_factory=datetime.now)
    txt_hash: int | None = None

    @property
    def names(self) -> tuple[str, ...]:
        """文字列へ."""
        if self.path is None:
            return ()
        if len(self.path) == 0:
            return ()
        ret = list(self.path)
        ret[-1] = self.title
        return tuple(ret)

    @classmethod
    def to_dict(cls, sn: SysNet) -> dict[str, str | list[str] | date | None]:
        """Resource meta info from sysnet."""
        tokens = sn.meta
        pubs = [n for n in tokens if n.type == "PUBLISHED"]
        if len(pubs) > 1:
            msg = "公開日(@published)は１つまで"
            raise ValueError(msg, pubs)
        pub = None if len(pubs) == 0 else parse2dt(pubs[0])
        return {
            "authors": [str(n) for n in tokens if n.type == "AUTHOR"],
            "urls": [str(n) for n in tokens if n.type == "URL"],
            "published": pub,
            "title": sn.root,
        }

    # parse を分離したい
    # しかし titleの取得には parseが必要
    # titleはtxtの一番上にあるので、そこだけをparseする
    # title parseを作成する
    # cache済みの場合は messageを変える
    @classmethod
    def from_str(
        cls,
        s: str,
        path: list[str] | None = None,
        updated: datetime | None = None,
    ) -> ResourceMeta:
        """文字列からリソースメタ情報を作成."""
        pubs = [parse2dt(p) for p in meta_parse(s, "@published")]
        meta = cls(
            title=title_parse(s),
            txt_hash=resource_text_hash(s),
            authors=meta_parse(s, "@author"),
            published=None if len(pubs) == 0 else pubs[0],
            urls=[Url(u) for u in meta_parse(s, "@url")],
        )
        if path is not None:
            meta.path = tuple(path)
        if updated is not None:
            meta.updated = updated
        return meta


class ResourceInfo(BaseModel):
    """リソースの所有者や統計を含む情報."""

    user: UserReadPublic
    folders: list[MFolder] = Field(default_factory=list)
    resource: MResource
    resource_stats: ResourceStats


class EntryDetail(BaseModel):
    """Entryと、その直下を表示するための情報."""

    user: UserReadPublic
    ancestors: list[MFolder] = Field(default_factory=list)
    entry: MFolder
    children: list[MFolder | MResource] = Field(default_factory=list)
    stats: dict[str, ResourceStats] = Field(default_factory=dict)


class ResourceDetail(BaseModel):
    """リソース詳細(API Return Type用)."""

    g: NXGraph  # Headを含む単文ネット
    resource_info: ResourceInfo
    uids: dict[UUID, KNode]
    terms: dict[UUID, Term]


# LResource由来
ResourceOrderKey = Literal["title", "published", "updated"]
# au3thors は listと比較しないといけない
# パフォーマンス悪化を懸念してやめとこう、単なるメタデータってことで
# やるんならauthorをLabelとして独立させてindexが効くようにすべし


# type をつけると get_argsで値のtupleが取れない
StatsOrderKey = Literal[  # LResourceStatsCache由来
    "n_char",
    "n_sentence",
    "r_isolation",
    "r_axiom",
    "r_unrefered",
    "average_degree",
    "density",
    "diameter",
    "radius",
]

UserOrderKey = Literal["username", "display_name"]  # LUser由来


class ResourceSearchResult(BaseModel, frozen=True):
    """リソース検索結果."""

    total: int
    data: list[ResourceInfo] = Field(default_factory=list)
