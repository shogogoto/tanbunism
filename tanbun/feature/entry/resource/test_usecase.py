"""test resource usecase.

cache 有無 / resource 有無 でテスト
"""

from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.entry.domain import ResourceMeta
from tanbun.feature.entry.label import LResource
from tanbun.feature.entry.namespace import create_resource, fetch_namespace
from tanbun.feature.entry.resource.repo.restore import restore_sysnet
from tanbun.feature.entry.resource.usecase import (
    preview_resource_update,
    save_resource_with_detail,
)
from tanbun.feature.user.label import LUser


@async_fixture()
async def u() -> LUser:  # noqa: D103
    return await LUser(email="one@gmail.com").save()


@mark_async_test()
async def test_save_resource_with_detail_unsaved_uncached(u: LUser):
    """Resource unsaved & cacheなし => resourceもcacheが作成される."""
    s = """
        # title1
          aaa
    """
    ns = await fetch_namespace(u.uid)
    assert len(ns.g.nodes) == 0
    assert len(ns.stats.values()) == 0

    await save_resource_with_detail(ns, s)
    ns = await fetch_namespace(u.uid)
    assert len(ns.stats.values()) == 1


@mark_async_test()
async def test_save_resource_with_detail_saved_uncached(u: LUser):
    """Resource 既にsaved & cacheなし => cacheが作成される."""
    s = """
        # title1
          aaa
    """
    await create_resource(u.uid, "# title1")
    ns = await fetch_namespace(u.uid)

    assert len(ns.g.nodes) == 1
    assert len(ns.stats.values()) == 0

    preview = await preview_resource_update(ns, s)
    assert preview.sentences_added == 1

    _resource, _meta, changed = await save_resource_with_detail(ns, s)
    ns = await fetch_namespace(u.uid)
    assert len(ns.g.nodes) == 1
    assert len(ns.stats.values()) == 1
    assert changed is True
    restored, _uids = await restore_sysnet(ns.get_resource("# title1").uid)
    assert set(restored.sentences) == {"aaa"}


@mark_async_test()
async def test_import_recovers_resource_detached_from_namespace(u: LUser) -> None:
    """一意キーだけ残ったResourceを再利用して名前空間へ接続し直す."""
    text = """# アジャイルサムライ
  iterative delivery
"""
    meta = ResourceMeta.from_str(text, ["it", "1アジャイルサムライ.kn"])
    orphan = await LResource(
        **meta.model_dump(),
        resource_key=f"{to_uuid(u.uid).hex}:{meta.title}",
    ).save()
    ns = await fetch_namespace(u.uid)
    assert ns.get_resource_or_none(meta.title) is None

    preview = await preview_resource_update(
        ns,
        text,
        ["it", "1アジャイルサムライ.kn"],
    )
    assert preview.sentences_added == 1

    resource, _meta, _changed = await save_resource_with_detail(
        ns,
        text,
        ["it", "1アジャイルサムライ.kn"],
    )

    repaired = await fetch_namespace(u.uid)
    assert resource.uid.hex == orphan.uid
    assert repaired.get_resource(meta.title).uid == resource.uid
    assert repaired.stats[resource.uid.hex].n_sentence == 1


@mark_async_test()
async def test_save_resource_with_detail_saved_cached(u: LUser):
    """Resource 既にsaved & cacheある => cacheが更新される."""
    s = """
        # title1
          aaa
    """
    ns = await fetch_namespace(u.uid)
    await save_resource_with_detail(ns, s)
    ns = await fetch_namespace(u.uid)
    rid = ns.get_resource("# title1").uid.hex
    assert len(ns.g.nodes) == 1
    assert ns.stats[rid].n_sentence == 1

    s2 = """
        # title1
          aaa
          bbb
    """
    await save_resource_with_detail(ns, s2)
    ns = await fetch_namespace(u.uid)
    assert len(ns.g.nodes) == 1
    assert ns.stats[rid].n_sentence == 2  # noqa: PLR2004


@mark_async_test()
async def test_save_identical_resource_is_noop(u: LUser) -> None:
    """同じ場所へ同一本文を再保存してもResourceの更新日時を変えない."""
    text = """# unchanged resource
  concept: same sentence
"""
    ns = await fetch_namespace(u.uid)
    first, _, first_changed = await save_resource_with_detail(
        ns,
        text,
        ["unchanged.tb"],
    )

    ns = await fetch_namespace(u.uid)
    second, _, second_changed = await save_resource_with_detail(
        ns,
        text,
        ["unchanged.tb"],
    )

    assert second.uid == first.uid
    assert second.updated == first.updated
    assert first_changed is True
    assert second_changed is False


@mark_async_test()
async def test_reimport_repairs_missing_root_and_headings(u: LUser) -> None:
    """同一本文の再importで欠損した本文構造を再生成する."""
    text = """# structured resource
## first
  first sentence
## second
  second sentence
"""
    ns = await fetch_namespace(u.uid)
    resource, _, _changed = await save_resource_with_detail(ns, text)
    _before, before_uids = await restore_sysnet(resource.uid)
    await adb.cypher_query(
        """
        MATCH (:Resource {uid: $uid})-[:BELOW|SIBLING*]->(head:Head)
        DETACH DELETE head
        """,
        params={"uid": resource.uid.hex},
    )

    ns = await fetch_namespace(u.uid)
    preview = await preview_resource_update(ns, text)
    repaired, _, changed = await save_resource_with_detail(ns, text)
    restored, repaired_uids = await restore_sysnet(repaired.uid)

    assert preview.sentences_added == 0
    assert changed is True
    assert set(restored.sentences) == {"first sentence", "second sentence"}
    assert repaired_uids["first sentence"] == before_uids["first sentence"]
    assert repaired_uids["second sentence"] == before_uids["second sentence"]
    assert {str(node) for node in restored.g if str(node).startswith("#")} == {
        "# structured resource",
        "## first",
        "## second",
    }


@mark_async_test()
async def test_reimport_repairs_partially_disconnected_headings(u: LUser) -> None:
    """先頭章が残っていても途中から切れた本文構造を再生成する."""
    text = """# partially disconnected resource
  @author test author
## first
  first sentence
## second
  パケット: packet
  説明: {パケット}
## third
  third sentence
"""
    ns = await fetch_namespace(u.uid)
    resource, _, _changed = await save_resource_with_detail(ns, text)
    _before, before_uids = await restore_sysnet(resource.uid)
    await adb.cypher_query(
        """
        MATCH ()-[relation:BELOW|SIBLING]->(head:Head {val: '## second'})
        DELETE relation
        """,
    )

    ns = await fetch_namespace(u.uid)
    repaired, _, changed = await save_resource_with_detail(ns, text)
    restored, repaired_uids = await restore_sysnet(repaired.uid)

    assert changed is True
    assert set(restored.sentences) == {
        "first sentence",
        "packet",
        "{パケット}",
        "third sentence",
    }
    definition = "{パケット}"
    assert repaired_uids[definition] == before_uids[definition]
    assert {str(node) for node in restored.g if str(node).startswith("#")} == {
        "# partially disconnected resource",
        "## first",
        "## second",
        "## third",
    }
