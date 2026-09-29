"""管理者向け孤立単文メンテナンスAPIのテスト."""

from uuid import uuid4

from httpx import AsyncClient
from neomodel import adb
from starlette import status

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.user.testing import aauth_header, aregister

from .domain import DeleteOrphanedTanbunsResult, OrphanedTanbun


async def _admin_headers(email: str) -> dict[str, str]:
    user = await aregister(email)
    user.is_superuser = True
    await user.save()
    return await aauth_header(email)


@mark_async_test()
async def test_admin_lists_only_sentences_without_location(ac: AsyncClient) -> None:
    """現行Resourceに配置された単文を除外し、孤立理由と参照数を返す."""
    owner = await aregister("orphan-owner@example.com")
    _, resource = await save_text(owner.uid, "# 配置あり\n  現行の単文\n")
    orphan_uid = uuid4().hex
    quiz_uid = uuid4().hex
    await adb.cypher_query(
        """
        CREATE (orphan:Sentence {
            uid: $orphan_uid,
            val: '配置を失った単文',
            resource_uid: $resource_uid
        })
        CREATE (:Quiz {uid: $quiz_uid})-[:QUIZ_TARGET]->(orphan)
        """,
        params={
            "orphan_uid": orphan_uid,
            "resource_uid": resource.uid.hex,
            "quiz_uid": quiz_uid,
        },
    )
    headers = await _admin_headers("orphan-admin@example.com")

    response = await ac.get("/admin/orphaned-tanbuns", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    items = [OrphanedTanbun.model_validate(item) for item in response.json()]
    assert len(items) == 1
    assert items[0].uid == orphan_uid
    assert items[0].resource_name == "# 配置あり"
    assert items[0].owner_email == owner.email
    assert items[0].reason == "missing_location"
    assert items[0].quiz_reference_count == 1


@mark_async_test()
async def test_admin_delete_retires_referenced_and_deletes_unreferenced(
    ac: AsyncClient,
) -> None:
    """クイズ参照は退役で保護し、それ以外の孤立単文は物理削除する."""
    owner = await aregister("orphan-delete-owner@example.com")
    _, resource = await save_text(owner.uid, "# 掃除対象\n  現行の単文\n")
    disposable_uid = uuid4().hex
    referenced_uid = uuid4().hex
    quiz_uid = uuid4().hex
    await adb.cypher_query(
        """
        CREATE (:Sentence {
            uid: $disposable_uid,
            val: '削除できる孤立単文',
            resource_uid: $resource_uid
        })
        CREATE (referenced:Sentence {
            uid: $referenced_uid,
            val: '保護する孤立単文',
            resource_uid: $resource_uid
        })
        CREATE (:Quiz {uid: $quiz_uid})-[:QUIZ_OPTION]->(referenced)
        """,
        params={
            "disposable_uid": disposable_uid,
            "referenced_uid": referenced_uid,
            "resource_uid": resource.uid.hex,
            "quiz_uid": quiz_uid,
        },
    )
    headers = await _admin_headers("orphan-delete-admin@example.com")

    response = await ac.post(
        "/admin/orphaned-tanbuns/delete",
        headers=headers,
        json={"sentence_ids": [disposable_uid, referenced_uid]},
    )

    assert response.status_code == status.HTTP_200_OK
    result = DeleteOrphanedTanbunsResult.model_validate(response.json())
    assert result.deleted_count == 1
    assert result.retired_count == 1
    assert result.skipped_count == 0
    rows, _ = await adb.cypher_query(
        """
        OPTIONAL MATCH (deleted {uid: $disposable_uid})
        OPTIONAL MATCH (retired:RetiredSentence {uid: $referenced_uid})
        OPTIONAL MATCH (:Quiz {uid: $quiz_uid})-[:BROKEN_BY]->(retired)
        RETURN count(DISTINCT deleted), count(DISTINCT retired)
        """,
        params={
            "disposable_uid": disposable_uid,
            "referenced_uid": referenced_uid,
            "quiz_uid": quiz_uid,
        },
    )
    assert rows == [[0, 1]]


@mark_async_test()
async def test_orphan_maintenance_requires_superuser(ac: AsyncClient) -> None:
    """通常ユーザーには監査データも削除操作も公開しない."""
    user = await aregister("orphan-regular@example.com")
    headers = await aauth_header(user.email)

    response = await ac.get("/admin/orphaned-tanbuns", headers=headers)

    assert response.status_code == status.HTTP_403_FORBIDDEN
