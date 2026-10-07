"""DB再計算・キュー・再開・上限の回帰テスト."""

# ruff: noqa: PLR2004

import asyncio
import json
from datetime import date
from math import log1p
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.dashboard import repo as dashboard
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.recommendation.settings import ReviewSettingsInput, save_settings
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aauth_header, aregister

from .domain import PageRankSettings, calculate_ranks
from .repo import PageRankStore
from .worker import process_job, start_worker, stop_worker


def test_reference_and_logic_direction() -> None:
    """参照先・前提に票が集まり、階層・自己辺・重複は影響しない."""
    nodes = ["a", "b", "c"]
    edges = [("a", "b", "RESOLVED"), ("b", "c", "TO")]
    ranks = calculate_ranks(nodes, edges)
    assert ranks["b"] > ranks["a"] == ranks["c"]
    assert sum(ranks.values()) == pytest.approx(1)
    assert ranks == calculate_ranks(
        nodes,
        [
            *edges,
            *edges,
            ("a", "b", "REF"),
            ("b", "a", "BELOW"),
            ("c", "c", "REF"),
            ("foreign", "a", "REF"),
        ],
    )


def test_empty_isolated_and_cyclic_graphs() -> None:
    """単文0・関係0・閉路でも有限時間で正常終了."""
    assert calculate_ranks([], []) == {}
    assert calculate_ranks(["a", "b"], []) == {"a": 0.5, "b": 0.5}
    assert calculate_ranks(["a", "b"], [("a", "b", "TO"), ("b", "a", "TO")]) == {
        "a": 0.5,
        "b": 0.5,
    }


async def seed(count: int = 1) -> list[str]:
    """import不要な既存Resourceと現行単文を用意する."""
    ids = [uuid4().hex for _ in range(count)]
    await adb.cypher_query(
        """UNWIND $ids AS id
        CREATE (r:Resource {uid: id, title: 'existing', txt_hash: 123,
          updated: datetime('2026-10-07T00:00:00Z')})
        CREATE (a:Sentence {uid: id + 'a', resource_uid: id, val: 'a'})
        CREATE (b:Sentence {uid: id + 'b', resource_uid: id, val: 'b'})
        CREATE (a)-[:RESOLVED]->(b)""",
        {"ids": ids},
    )
    await adb.cypher_query(
        "CREATE CONSTRAINT pagerank_test_queue IF NOT EXISTS "
        "FOR (q:PageRankQueue) REQUIRE q.key IS UNIQUE",
    )
    return ids


@mark_async_test()
async def test_db_only_recalculation_and_freshness(mocker) -> None:
    """ファイルなしで単文値を保存し、更新後はstale。XPを作らない."""
    [resource_id] = await seed()
    notify = mocker.patch(
        "tanbun.feature.recommendation.pagerank.worker.notify_user",
        new_callable=AsyncMock,
    )
    store = PageRankStore()
    assert (await store.resources())[0]["state"] == "missing"
    await store.enqueue([resource_id], uuid4().hex)
    job = await store.claim("owner", 1)
    snapshot = await store.graph(resource_id, PageRankSettings())
    checks, _ = await adb.cypher_query(
        "MATCH (j:PageRankJob {uid:$job}), (r:Resource {uid:$rid}) "
        "RETURN j.lease_until > datetime(), r.txt_hash = $hash, "
        "toString(r.updated) = $updated",
        {
            "job": job["uid"],
            "rid": resource_id,
            "hash": snapshot[1],
            "updated": snapshot[2],
        },
    )
    assert checks == [[True, True, True]], (checks, snapshot[2])
    await process_job(store, job)
    [saved] = await store.jobs()
    assert saved["status"] == "completed", saved
    assert saved["completed"] == saved["total"] == 1
    assert (await store.resources())[0]["state"] == "ready"
    ranks, _ = await adb.cypher_query(
        "MATCH (s:Sentence {resource_uid:$uid}) "
        "RETURN s.val, s.pagerank, s.pagerank_score ORDER BY s.val",
        {"uid": resource_id},
    )
    assert ranks[1][1] > ranks[0][1]
    assert sum(row[1] for row in ranks) == pytest.approx(1)
    assert sum(row[2] for row in ranks) == pytest.approx(2)
    assert notify.await_count == 1
    assert notify.call_args.args[1].href == "/admin?view=pagerank"
    xp, _ = await adb.cypher_query("MATCH (e:ResourceXpEvent) RETURN count(e)")
    assert xp == [[0]]
    await adb.cypher_query(
        "MATCH (r:Resource {uid:$uid}) SET r.txt_hash=124",
        {"uid": resource_id},
    )
    assert (await store.resources())[0]["state"] == "stale"


@mark_async_test()
async def test_changed_resource_does_not_publish() -> None:
    """計算中の更新を検知して古いキャッシュを書かない."""
    [uid] = await seed()
    store = PageRankStore()
    await store.enqueue([uid], uuid4().hex)
    job = await store.claim("owner", 1)
    snapshot = await store.graph(uid, PageRankSettings())
    await adb.cypher_query(
        "MATCH (r:Resource {uid:$uid}) SET r.txt_hash=124",
        {"uid": uid},
    )
    with pytest.raises(ValueError, match="更新"):
        await store.publish(
            job,
            uid,
            snapshot,
            calculate_ranks(snapshot[3], snapshot[4]),
        )
    assert (await store.resources())[0]["state"] == "missing"


@mark_async_test()
async def test_fifo_limits_and_expired_lease_resume() -> None:
    """全workerで実行上限を共有し、再起動後は保存済み位置から再開."""
    ids = await seed(2)
    store = PageRankStore()
    await store.save_settings(PageRankSettings(max_pending_jobs=2))
    first = await store.enqueue(ids, uuid4().hex)
    second = await store.enqueue(ids, uuid4().hex)
    with pytest.raises(ValueError, match="満杯"):
        await store.enqueue(ids, uuid4().hex)
    job = await store.claim("old", 1)
    assert job["uid"] == first["uid"]
    assert await store.claim("other", 1) is None
    results = [{"resource_id": ids[0], "title": "existing", "error": None}]
    await store.checkpoint(job, results)
    await adb.cypher_query(
        "MATCH (j:PageRankJob {uid:$uid}) "
        "SET j.lease_until=datetime()-duration({seconds:1})",
        {"uid": job["uid"]},
    )
    resumed = await store.claim("new", 1)
    assert resumed["uid"] == first["uid"]
    assert resumed["completed"] == 1
    assert json.loads(resumed["results"]) == results
    assert not await store.heartbeat(first["uid"], "old")
    assert not await store.checkpoint(job, results, finished=True)
    await store.checkpoint(resumed, results, finished=True)
    assert (await store.claim("next", 1))["uid"] == second["uid"]


@mark_async_test()
async def test_concurrent_claim_has_one_winner() -> None:
    """並列に複数プロセス相当の取得を行っても上限を超えない."""
    ids = await seed()
    store = PageRankStore()
    for _ in range(3):
        await store.enqueue(ids, uuid4().hex)
    claimed = await asyncio.gather(*(store.claim(str(i), 1) for i in range(6)))
    assert sum(job is not None for job in claimed) == 1


@mark_async_test()
async def test_sequential_failure_continues_and_resume_skips_completed(mocker) -> None:
    """再開で済んだリソースは計算せず、一件の失敗でも次に進む."""
    ids = await seed(3)
    mocker.patch(
        "tanbun.feature.recommendation.pagerank.worker.notify_user",
        new_callable=AsyncMock,
    )
    store = PageRankStore()
    await store.enqueue(ids, uuid4().hex)
    job = await store.claim("owner", 1)
    job["results"] = json.dumps([
        {"resource_id": ids[0], "title": "done", "error": None},
    ])
    await adb.cypher_query(
        "MATCH (r:Resource {uid:$uid}) DETACH DELETE r",
        {"uid": ids[1]},
    )
    load = mocker.spy(store, "graph")
    await process_job(store, job)
    assert [call.args[0] for call in load.call_args_list] == ids[1:]
    [saved] = await store.jobs()
    assert saved["status"] == "partial"
    assert saved["completed"] == 3
    assert saved["results"][1]["error"]
    assert saved["results"][2]["error"] is None


@mark_async_test()
async def test_graph_size_and_cross_resource_boundaries() -> None:
    """外部リソース・退役単文は含めず、サイズ上限で停止."""
    first, second = await seed(2)
    await adb.cypher_query(
        """MATCH (a:Sentence {resource_uid:$a}), (b:Sentence {resource_uid:$b})
        CREATE (a)-[:RESOLVED]->(b)
        CREATE (old:RetiredSentence {uid:$old, resource_uid:$a})
        CREATE (a)-[:RESOLVED]->(old)""",
        {"a": first, "b": second, "old": uuid4().hex},
    )
    store = PageRankStore()
    snapshot = await store.graph(first, PageRankSettings())
    assert len(snapshot[3]) == 2
    assert len(snapshot[4]) == 1
    with pytest.raises(ValueError, match="上限"):
        await store.graph(first, PageRankSettings(max_nodes=1))
    incomplete = uuid4().hex
    await adb.cypher_query(
        "CREATE (:Resource {uid:$uid})",
        {"uid": incomplete},
    )
    assert (
        next(r for r in await store.resources() if r["uid"] == incomplete)["title"]
        == "無題"
    )
    with pytest.raises(ValueError, match="importが完了していません"):
        await store.graph(incomplete, PageRankSettings())


@mark_async_test()
async def test_admin_api_authorization_validation_and_queue(ac) -> None:
    """受付は202ですぐ返し、実計算はworkerに任せる."""
    ids = await seed()
    admin = await aregister("pagerank-admin@example.com")
    admin.is_superuser = True
    await admin.save()
    headers = await aauth_header(admin.email)
    regular = await aregister("pagerank-user@example.com")
    user_headers = await aauth_header(regular.email)
    for method, path in [
        ("GET", "resources"),
        ("GET", "jobs"),
        ("GET", "settings"),
        ("PUT", "settings"),
        ("POST", "jobs"),
    ]:
        body = {"resource_ids": ids} if path == "jobs" else {}
        assert (
            await ac.request(method, f"/admin/pagerank/{path}", json=body)
        ).status_code == 401
        assert (
            await ac.request(
                method,
                f"/admin/pagerank/{path}",
                headers=user_headers,
                json=body,
            )
        ).status_code == 403
    path = "/admin/pagerank/jobs"
    assert (
        await ac.post(path, headers=headers, json={"resource_ids": []})
    ).status_code == 422
    assert (
        await ac.post(path, headers=headers, json={"resource_ids": ["bad"]})
    ).status_code == 422
    assert (
        await ac.post(path, headers=headers, json={"resource_ids": [uuid4().hex]})
    ).status_code == 409
    res = await ac.post(
        path,
        headers=headers,
        json={"resource_ids": [str(UUID(ids[0])), ids[0]]},
    )
    assert res.status_code == 202
    assert res.json()["status"] == "queued"
    assert res.json()["total"] == 1
    assert "owner" not in res.json()
    assert (await ac.get(path, headers=headers)).json()[0]["uid"] == res.json()["uid"]
    settings_path = "/admin/pagerank/settings"
    assert (
        await ac.put(settings_path, headers=headers, json={"max_concurrent_jobs": 0})
    ).status_code == 422
    assert (
        await ac.put(settings_path, headers=headers, json={"max_concurrent_jobs": 2})
    ).status_code == 200
    assert (await ac.get(settings_path, headers=headers)).json()[
        "max_concurrent_jobs"
    ] == 2


@mark_async_test()
async def test_real_worker_consumes_queue_without_http_context(mocker) -> None:
    """lifespanと同じworkerが独立した接続で処理し、停止時に接続を片付ける."""
    ids = await seed()
    done = asyncio.Event()
    mocker.patch(
        "tanbun.feature.recommendation.pagerank.worker.notify_user",
        new_callable=AsyncMock,
        side_effect=lambda *_args: done.set(),
    )
    store = PageRankStore()
    await store.enqueue(ids, uuid4().hex)
    task = start_worker()

    try:
        await asyncio.wait_for(done.wait(), timeout=10)
        assert (await store.jobs())[0]["status"] == "completed"
    finally:
        await stop_worker(task)
    assert task.done()


@mark_async_test()
async def test_review_uses_only_fresh_ranks_and_falls_back(mocker) -> None:
    """日替り選択の候補段階からPageRankを使い、古い結果は関連数に戻す."""
    user = await LUser(email="rank-review@example.com").save()
    _, resource = await save_text(user.uid, "# ranked\n  first\n  second\n")
    await adb.cypher_query(
        """MATCH (r:Resource {uid:$uid})
        SET r.pagerank_version=1, r.pagerank_source_hash=r.txt_hash,
          r.pagerank_source_updated=r.updated
        WITH r MATCH (s:Sentence {resource_uid:r.uid})
        SET s.pagerank_score=CASE WHEN s.val='first' THEN 9.0 ELSE 0.1 END""",
        {"uid": resource.uid.hex},
    )
    await save_settings(
        user.uid,
        ReviewSettingsInput(name="rank", priority="pagerank"),
        "default",
    )
    selection = mocker.spy(dashboard, "select_daily")
    items = await dashboard.list_personal_tanbuns(user.uid, date(2026, 10, 7))
    names = {item.uid.hex: item.sentence for item in items}
    weights = {names[c.uid]: c.weight for c in selection.call_args_list[0].args[0]}
    assert weights["first"] / weights["second"] == pytest.approx(
        (1 + log1p(9)) / (1 + log1p(0.1)),
    )
    await adb.cypher_query(
        "MATCH (r:Resource {uid:$uid}) SET r.pagerank_source_hash = -1",
        {"uid": resource.uid.hex},
    )
    selection.reset_mock()
    await dashboard.list_personal_tanbuns(user.uid, date(2026, 10, 8))
    weights = [c.weight for c in selection.call_args_list[0].args[0]]
    assert weights[0] == weights[1]


@mark_async_test()
async def test_empty_resource_is_valid_and_stale_owner_cannot_publish(mocker) -> None:
    """単文0も完了。リース再取得後は古いworkerの書込を拒否する."""
    [uid] = await seed()
    store = PageRankStore()
    mocker.patch(
        "tanbun.feature.recommendation.pagerank.worker.notify_user",
        new_callable=AsyncMock,
    )
    await store.enqueue([uid], uuid4().hex)
    old = await store.claim("old", 1)
    snapshot = await store.graph(uid, PageRankSettings())
    await adb.cypher_query(
        "MATCH (j:PageRankJob {uid:$uid}) "
        "SET j.lease_until=datetime()-duration({seconds:1})",
        {"uid": old["uid"]},
    )
    current = await store.claim("new", 1)
    with pytest.raises(ValueError, match="所有権"):
        await store.publish(
            old,
            uid,
            snapshot,
            calculate_ranks(snapshot[3], snapshot[4]),
        )
    await adb.cypher_query(
        "MATCH (s:Sentence {resource_uid:$uid}) DETACH DELETE s",
        {"uid": uid},
    )
    await process_job(store, current)
    assert (await store.jobs())[0]["status"] == "completed"
    assert (await store.resources())[0]["state"] == "ready"
