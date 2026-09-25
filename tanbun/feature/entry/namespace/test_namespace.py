"""test repo."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.entry.domain import NameSpace, ResourceMeta, resource_text_hash
from tanbun.feature.entry.errors import DuplicatedTitleError
from tanbun.feature.entry.label import LResource
from tanbun.feature.entry.namespace import (
    fetch_namespace,
    save_or_move_resource,
    save_resource,
    sync_namespace,
)
from tanbun.feature.user.label import LUser

from .sync import Anchor


def write_text(p: Path, txt: str) -> Path:  # noqa: D103
    p.write_text(dedent(txt), encoding="utf-8")
    return p


def create_test_files(base_path: Path) -> tuple[Anchor, list[Path]]:
    """sync用テストファイル群."""
    subs = [
        base_path / "sub1" / "sub11",
        base_path / "sub1" / "sub12",
        base_path / "sub2",
    ]
    [s.mkdir(parents=True, exist_ok=True) for s in subs]
    sub11, _sub12, sub2 = subs

    title1 = write_text(
        sub11 / "title1.txt",
        """
        # title1
            @author John Due
            @published H20/11/1
        ## xxx
            x
            y
            z
        """,
    )

    title2 = write_text(
        sub11 / "title2.txt",
        """
        # title2
            @author hanako
            @published S20/11/1
        ## abc
            janne da arc
        """,
    )
    direct = write_text(
        base_path / "direct.txt",
        """
        # direct
            @published 1999/12/31
        """,
    )
    fail = write_text(
        sub2 / "fail.txt",
        """
        # fail
            @author nanashi
        ## hatena
            blog
                -> xxx
               -> yyy
        """,
    )
    return Anchor(base_path), [title1, title2, direct, fail]


@async_fixture()
def files(tmp_path: Path) -> tuple[Anchor, list[Path]]:  # noqa: D103
    return create_test_files(tmp_path)


type Fixture = tuple[LUser, Anchor, list[Path], NameSpace]


async def setup(p: Path) -> Fixture:  # noqa: D103
    u = await LUser().save()
    anchor, paths = create_test_files(p)
    meta = anchor.to_metas(paths)
    ns = await fetch_namespace(u.uid)
    for m in meta.root:
        await save_resource(m, ns)
    ns = await fetch_namespace(u.uid)
    return u, anchor, paths, ns


@mark_async_test()
async def test_save_new(tmp_path: Path) -> None:
    """全て新規."""
    _, _, _, ns = await setup(tmp_path)

    assert ns.get_or_none("sub1")
    assert ns.get_or_none("sub1", "sub11")
    assert ns.get_or_none("sub1", "sub11", "# title1")
    assert ns.get_or_none("sub1", "sub11", "# title2")
    assert ns.get_or_none("# direct")


@mark_async_test()
async def test_save_halfway_exists_folder(tmp_path: Path) -> None:
    """途中のフォルダまで既存."""
    u, anchor, paths, ns = await setup(tmp_path)  # 既存作成
    sub = paths[0].parent  # anchor / sub1 / sub11

    p = sub / "new1" / "new2"
    p.mkdir(parents=True)
    new = write_text(
        p / "added.txt",
        """
        # added
            @author GTO
        ## heading
            a
            b
            c
        """,
    )
    meta = anchor.to_metas([*paths, new])
    ns = await fetch_namespace(u.uid)
    for m in meta.root:
        await save_resource(m, ns)
    ns = await fetch_namespace(u.uid)
    assert ns.get_or_none("sub1")
    assert ns.get_or_none("sub1", "sub11")
    assert ns.get_or_none("sub1", "sub11", "# title1")
    assert ns.get_or_none("# direct")
    assert ns.get_or_none("sub1", "sub11", "new1", "new2", "# added")


@mark_async_test()
async def test_save_update_exists_hash(tmp_path: Path) -> None:
    """既存リソースの更新."""
    u, anchor, paths, ns = await setup(tmp_path)
    tgt = paths[0]
    tgt = write_text(
        tgt,
        """
        # title1
            @author John Due
            @published H20/11/1
        ## xxx -> aaa
            x -> a
            y -> b
            z -> c
        """,
    )
    assert ns.get("sub1", "sub11", "# title1").txt_hash != resource_text_hash(
        tgt.read_text(),
    )
    paths[0] = tgt
    meta = anchor.to_metas(paths)
    for m in meta.root:
        await save_resource(m, ns)
    ns = await fetch_namespace(u.uid)
    assert ns.get("sub1", "sub11", "# title1").txt_hash == resource_text_hash(
        tgt.read_text(),
    )


@mark_async_test()
async def test_sync_move(tmp_path: Path) -> None:
    """移動したResourceを検知."""
    u, anchor, paths, ns = await setup(tmp_path)
    tgt = paths[0]
    paths[0] = tgt.rename(tgt.parent.parent / tgt.name)  # 2階上に移動
    meta = anchor.to_metas(paths)
    assert ns.get_or_none("sub1", "sub11", "# title1")
    uplist = await sync_namespace(meta, ns)

    ns = await fetch_namespace(u.uid)
    assert ns.get_or_none("sub1", "sub11", "# title1") is None  # たまに失敗
    assert ns.get_or_none("sub1", "# title1")
    assert [anchor / p for p in uplist] == [paths[0]]


@mark_async_test()
async def test_move_resource(tmp_path: Path) -> None:
    """重複したタイトルの追加は失敗させる."""
    _u, _anchor, _paths, ns = await setup(tmp_path)
    m = ResourceMeta(title="# title3")  # 新規タイトルをuser直下へ
    await save_or_move_resource(m, ns)
    assert ns.get_or_none("# title3")
    # 既存タイトルを違う場所に追加
    m = ResourceMeta(title="# title3", path=("sub1", "xxx"))
    await save_or_move_resource(m, ns)
    assert not ns.get_or_none("# title3")
    assert ns.get_or_none("sub1", "# title3")

    m = ResourceMeta(title="# title3", path=("sub1", "sub2", "xxx"))
    await save_or_move_resource(m, ns)
    assert not ns.get_or_none("sub1", "# title3")
    assert ns.get_or_none("sub1", "sub2", "# title3")

    m = ResourceMeta(title="# title3")  # 新規タイトルをuser直下へ
    await save_or_move_resource(m, ns)
    assert not ns.get_or_none("sub1", "sub2", "# title3")
    assert ns.get_or_none("# title3")


@mark_async_test()
async def test_duplicate_title(tmp_path: Path) -> None:
    """重複したタイトルの追加は失敗させる."""
    _u, anchor, paths, ns = await setup(tmp_path)

    metas = anchor.to_metas(paths)
    metas.root.extend(
        [
            ResourceMeta(title="# title3", path=("xxx",)),
            ResourceMeta(title="# title3", path=("sub1", "xxx")),
        ],
    )

    with pytest.raises(DuplicatedTitleError):
        await sync_namespace(metas, ns)


@mark_async_test()
async def test_save_resource_updated_without_hyphen():
    """リソースの更新日時が更新されuidにハイフンが含まれない."""
    u = await LUser().save()
    ns = await fetch_namespace(u.uid)

    await save_or_move_resource(
        ResourceMeta(title="# title1", path=("sub1", "sub11")),
        ns,
    )

    ns = await fetch_namespace(u.uid)
    r = ns.get_resource("# title1")
    uid = r.uid.hex
    assert r.updated

    assert await LResource.nodes.get(uid=uid)
    all_ = await LResource.nodes.filter()
    assert len(all_) == 1
    await save_or_move_resource(
        ResourceMeta(title="# title1", path=("sub1", "sub11")),
        ns,
    )
    ns = await fetch_namespace(u.uid)
    r2 = ns.get_resource("# title1")
    assert r.updated < r2.updated
    assert await LResource.nodes.get(uid=uid)
    all_ = await LResource.nodes.filter()
    assert len(all_) == 1
    await save_or_move_resource(
        ResourceMeta(title="# title1", path=("sub2", "sub12")),
        ns,
    )
    ns = await fetch_namespace(u.uid)
    r3 = ns.get_resource("# title1")
    assert r2.updated < r3.updated
    assert await LResource.nodes.get(uid=uid)
    all_ = await LResource.nodes.filter()
    assert len(all_) == 1


@mark_async_test()
async def test_save_resource_stats() -> None:
    """リソース統計情報を保存."""
    u = await LUser().save()
    ns = await fetch_namespace(u.uid)

    await save_or_move_resource(ResourceMeta(title="# title1"), ns)
