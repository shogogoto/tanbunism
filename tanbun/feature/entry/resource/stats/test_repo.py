"""test repo."""

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.domain import ResourceMeta
from tanbun.feature.entry.namespace import (
    create_resource,
    fetch_info_by_resource_uid,
    fetch_namespace,
    save_or_move_resource,
)
from tanbun.feature.parsing.tree2net import parse2net
from tanbun.feature.user.label import LUser

from .repo import fetch_resource_stats_cache, save_resource_stats_cache


@mark_async_test()
async def test_new_and_update_stat_cache():
    """リソース統計情報の新規作成と更新."""
    u = await LUser(email="one@gmail.com").save()
    lr = await create_resource(u.uid, "# r1")

    s = """
        # r1
        ## title1
          aaa
          bbb
    """
    sn = parse2net(s)

    lb1 = await save_resource_stats_cache(lr.uid, sn)
    lb2 = await save_resource_stats_cache(lr.uid, sn)
    assert lb1.element_id == lb2.element_id


@mark_async_test()
async def test_fetch_resource_info_by_resource_uid() -> None:
    """Resource UIDからResourceMetaを取得."""
    u = await LUser(email="one@gmail.com").save()
    m = ResourceMeta(title="# title1", path=("sub1", "filename"))
    s = """
        # title1
          aaa
    """
    ns = await fetch_namespace(u.uid)
    await save_or_move_resource(m, ns)

    ns = await fetch_namespace(u.uid)
    r = ns.get_resource("# title1")
    assert r
    sn = parse2net(s)
    assert await fetch_resource_stats_cache(r.uid) is None
    await save_resource_stats_cache(r.uid, sn)

    info = await fetch_info_by_resource_uid(r.uid)
    assert info.user.id.hex == u.uid
    assert info.resource.uid == r.uid
    assert info.resource.path == ("sub1",)
    assert [folder.name for folder in info.folders] == ["sub1"]
    assert info.resource_stats is not None

    assert await fetch_resource_stats_cache(r.uid)
