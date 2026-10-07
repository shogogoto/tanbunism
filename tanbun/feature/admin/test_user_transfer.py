"""管理者によるUserを消さない一括移行の回帰テスト."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from neomodel import adb
from starlette import status

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aauth_header, aregister


async def _accounts() -> tuple[LUser, LUser, dict[str, str]]:
    source = await aregister("aaa@a.com")
    target = await aregister("google@example.com")
    admin = await aregister("transfer-admin@example.com")
    admin.is_superuser = True
    await admin.save()
    return source, target, await aauth_header(admin.email)


def _request(preview: dict) -> dict:
    return {
        "target_id": preview["target_id"],
        "source_confirmation": preview["source_email"],
        "target_confirmation": preview["target_email"],
        "preview_token": preview["preview_token"],
    }


@mark_async_test()
async def test_transfer_preserves_users_auth_and_all_learning_data(ac: AsyncClient):
    """双方の認証を保持し、所有辺・履歴・日別キーを移して再送に備える."""
    source, target, headers = await _accounts()
    _, resource = await save_text(source.uid, "# transfer book\n  term: sentence\n")
    quiz_id, answer_id = uuid4().hex, uuid4().hex
    await adb.cypher_query(
        """MATCH (s:User {uid:$source}), (t:User {uid:$target})
        SET s.username='original', t.username='google'
        CREATE (s)-[:OAUTH]->(:Account {account_id:'source-account'}),
            (t)-[:OAUTH]->(:Account {account_id:'google-account'}),
            (s)-[:CREATE {note:'keep'}]->(q:Quiz {uid:$quiz}),
            (s)-[:LEARN]->(q),
            (s)-[:ANSWER]->(a:Answer {uid:$answer})-[:ANSWER_OF]->(q),
            (s)-[:REPORT]->(:QuizReport {uid:'report', key:$report})-[:REPORT_OF]->(q),
            (s)-[:RECOMMENDATIONS]->(:DailyRecommendation {
                ids:[$quiz], scope:'quizzes', day:date()}),
            (s)-[:REVIEW_SETTINGS]->(:ReviewSettings {id:'custom', config:'{}'}),
            (s)-[:REVIEW_DAY]->(:ReviewDaySettings {
                id:'custom', day:date(), config:'{}'}),
            (:Notification {uid:'source-note', href:$old_profile})-[:OWNED]->(s),
            (:PushSubscription {endpoint:'source-browser'})-[:OWNED]->(s),
            (t)-[:RECOMMENDATIONS]->(:DailyRecommendation {
                ids:[], scope:'quizzes', day:date()}),
            (t)-[:REVIEW_DAY]->(:ReviewDaySettings {
                id:'default', day:date(), config:'{}'}),
            (:Notification {uid:'target-note'})-[:OWNED]->(t),
            (:TanbunExposure {user_id:$source, key:$exposure,
                sentence_id:'sentence', seen_on:date()}),
            (:ResourceXpEvent {user_id:$source, key:$xp, xp:8,
                resource_id:$resource})""",
        params={
            "source": source.uid,
            "target": target.uid,
            "quiz": quiz_id,
            "answer": answer_id,
            "resource": resource.uid.hex,
            "report": f"{source.uid}:{quiz_id}",
            "exposure": f"{source.uid}:sentence:2026-10-08",
            "xp": f"{source.uid}:{answer_id}:2026-10-08:quiz_answer",
            "old_profile": f"/user/{UUID(source.uid)}",
        },
    )
    path = f"/admin/users/{source.uid}"
    response = await ac.get(
        f"{path}/transfer-preview",
        params={"target_id": target.uid},
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    preview = response.json()
    assert not preview["blockers"]
    assert preview["counts"]["Resource"] == preview["counts"]["Quiz"] == 1
    assert (
        await ac.post(f"{path}/transfer", json=_request(preview), headers=headers)
    ).status_code == status.HTTP_200_OK
    rows, _ = await adb.cypher_query(
        """MATCH (s:User {uid:$source}), (t:User {uid:$target})
        MATCH (s)-[:OAUTH]->(sa:Account), (t)-[:OAUTH]->(ta:Account)
        MATCH (r:Resource {uid:$resource})-[:PARENT*0..32]->()-[:OWNED]->(t)
        MATCH (t)-[c:CREATE]->(q:Quiz {uid:$quiz})
        MATCH (t)-[:ANSWER]->(:Answer {uid:$answer})
        MATCH (:PushSubscription {endpoint:'source-browser'})-[:OWNED]->(s)
        MATCH (n:Notification {uid:'source-note'})-[:OWNED]->(t)
        RETURN s.email, t.email, sa.account_id, ta.account_id,
            s.username, t.username, r.resource_key, c.note, n.href""",
        params={
            "source": source.uid,
            "target": target.uid,
            "resource": resource.uid.hex,
            "quiz": quiz_id,
            "answer": answer_id,
        },
    )
    assert rows == [
        [
            "aaa@a.com",
            "google@example.com",
            "source-account",
            "google-account",
            "original",
            "google",
            f"{target.uid}:{resource.name}",
            "keep",
            f"/user/{UUID(target.uid)}",
        ],
    ]
    rows, _ = await adb.cypher_query(
        """MATCH (e:ResourceXpEvent {user_id:$target}),
            (x:TanbunExposure {user_id:$target}),
            (:User {uid:$target})-[:REPORT]->(r:QuizReport)
        RETURN e.key, e.xp, x.key, r.key""",
        params={"target": target.uid},
    )
    assert rows == [
        [
            f"{target.uid}:{answer_id}:2026-10-08:quiz_answer",
            8,
            f"{target.uid}:sentence:2026-10-08",
            f"{target.uid}:{quiz_id}",
        ],
    ]
    # 再実行は拒否し、何も複製しない。
    assert (
        await ac.post(f"{path}/transfer", json=_request(preview), headers=headers)
    ).status_code == status.HTTP_409_CONFLICT
    # 所有者別Resourceキーで再import時にも同じResourceを発見できる。
    rows, _ = await adb.cypher_query(
        "MATCH (r:Resource {resource_key:$key}) RETURN r.uid",
        params={"key": f"{target.uid}:{resource.name}"},
    )
    assert rows == [[resource.uid.hex]]


@mark_async_test()
async def test_transfer_requires_admin_and_confirmations(ac: AsyncClient):
    """一般ユーザーとメール確認失敗では元データを保持."""
    source, target, headers = await _accounts()
    await save_text(source.uid, "# source\n  sentence\n")
    path = f"/admin/users/{source.uid}"
    params = {"target_id": target.uid}
    assert (
        await ac.get(f"{path}/transfer-preview", params=params)
    ).status_code == status.HTTP_401_UNAUTHORIZED
    user_headers = await aauth_header(source.email)
    assert (
        await ac.get(f"{path}/transfer-preview", params=params, headers=user_headers)
    ).status_code == status.HTTP_403_FORBIDDEN
    preview = (
        await ac.get(f"{path}/transfer-preview", params=params, headers=headers)
    ).json()
    body = {**_request(preview), "source_confirmation": "wrong@example.com"}
    assert (
        await ac.post(f"{path}/transfer", json=body, headers=headers)
    ).status_code == status.HTTP_409_CONFLICT
    unchanged = (
        await ac.get(f"{path}/transfer-preview", params=params, headers=headers)
    ).json()
    assert unchanged == preview


@mark_async_test()
@pytest.mark.parametrize("change", ["source", "target"])
async def test_transfer_rechecks_preview_and_rejects_existing_data(
    ac: AsyncClient,
    change: str,
):
    """プレビュー後の変更・既存の移行先データは上書きしない."""
    source, target, headers = await _accounts()
    await save_text(source.uid, "# source\n  sentence\n")
    path = f"/admin/users/{source.uid}"
    params = {"target_id": target.uid}
    preview = (
        await ac.get(f"{path}/transfer-preview", params=params, headers=headers)
    ).json()
    await save_text(
        source.uid if change == "source" else target.uid,
        "# new data\n  newer\n",
    )
    response = await ac.post(
        f"{path}/transfer",
        json=_request(preview),
        headers=headers,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "再確認" in response.text if change == "source" else "既存" in response.text
    assert (await ac.get(f"{path}/resources", headers=headers)).json()


@mark_async_test()
async def test_transfer_rolls_back_on_constraint_failure(ac: AsyncClient):
    """キーの衝突による途中失敗でも、Resource・XP・辺を部分更新しない."""
    source, target, headers = await _accounts()
    _, resource = await save_text(source.uid, "# source\n  sentence\n")
    await adb.cypher_query(
        """CREATE CONSTRAINT transfer_test_key IF NOT EXISTS
        FOR (e:ResourceXpEvent) REQUIRE e.key IS UNIQUE""",
    )
    await adb.cypher_query(
        """CREATE (:ResourceXpEvent {key:$old, user_id:$source, xp:1}),
            (:ResourceXpEvent {key:$new, user_id:'third-user', xp:1})""",
        params={
            "old": f"{source.uid}:collision",
            "new": f"{target.uid}:collision",
            "source": source.uid,
        },
    )
    path = f"/admin/users/{source.uid}"
    params = {"target_id": target.uid}
    preview = (
        await ac.get(f"{path}/transfer-preview", params=params, headers=headers)
    ).json()
    assert (
        await ac.post(f"{path}/transfer", json=_request(preview), headers=headers)
    ).status_code >= status.HTTP_400_BAD_REQUEST
    rows, _ = await adb.cypher_query(
        "MATCH (r:Resource {uid:$rid}) RETURN r.resource_key",
        params={"rid": resource.uid.hex},
    )
    assert rows == [[f"{source.uid}:{resource.name}"]]
    assert (
        await ac.get(f"{path}/transfer-preview", params=params, headers=headers)
    ).json() == preview
