"""管理者向け孤立単文メンテナンスAPIのテスト."""

from uuid import uuid4

from httpx import AsyncClient
from neomodel import adb
from starlette import status

from tanbun.conftest import mark_async_test
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.entry.resource.import_settings import ResourceImportSettings
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.quiz.learning.study_plan.preparation_settings import (
    QuizPreparationSettings,
)
from tanbun.feature.user.testing import aauth_header, aregister

from .domain import DeleteOrphanedTanbunsResult, OrphanedTanbun


async def _admin_headers(email: str) -> dict[str, str]:
    user = await aregister(email)
    user.is_superuser = True
    await user.save()
    return await aauth_header(email)


@mark_async_test()
async def test_admin_updates_quiz_preparation_settings(ac: AsyncClient) -> None:
    """adminは一括クイズ準備の負荷上限を更新できる."""
    headers = await _admin_headers("quiz-settings-admin@example.com")
    defaults = await ac.get(
        "/admin/settings/quiz-preparation",
        headers=headers,
    )
    assert defaults.status_code == status.HTTP_200_OK
    assert QuizPreparationSettings.model_validate(defaults.json()) == (
        QuizPreparationSettings()
    )

    updated = QuizPreparationSettings(
        max_concurrent_jobs=2,
        max_concurrent_jobs_per_user=1,
        max_quizzes_per_job=300,
    )
    response = await ac.put(
        "/admin/settings/quiz-preparation",
        headers=headers,
        json=updated.model_dump(),
    )

    assert response.status_code == status.HTTP_200_OK
    assert QuizPreparationSettings.model_validate(response.json()) == updated


@mark_async_test()
async def test_admin_updates_resource_import_settings(ac: AsyncClient) -> None:
    """adminはResource importの同時実行数を更新できる."""
    headers = await _admin_headers("import-settings-admin@example.com")
    updated = ResourceImportSettings(
        max_concurrent_imports=2,
        max_concurrent_imports_per_user=1,
    )

    response = await ac.put(
        "/admin/settings/resource-import",
        headers=headers,
        json=updated.model_dump(),
    )
    fetched = await ac.get(
        "/admin/settings/resource-import",
        headers=headers,
    )

    assert response.status_code == status.HTTP_200_OK
    assert ResourceImportSettings.model_validate(response.json()) == updated
    assert ResourceImportSettings.model_validate(fetched.json()) == updated


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


@mark_async_test()
async def test_admin_can_suspend_and_restore_user(ac: AsyncClient) -> None:
    """adminは通常ユーザーを停止・再開できるが、自分自身は停止できない."""
    target = await aregister("suspend-target@example.com")
    target_headers = await aauth_header(target.email)
    admin = await aregister("suspend-admin@example.com")
    admin.is_superuser = True
    await admin.save()
    headers = await aauth_header(admin.email)

    users = await ac.get("/admin/users", headers=headers)
    assert users.status_code == status.HTTP_200_OK
    target_item = next(item for item in users.json() if item["email"] == target.email)
    assert target_item["is_active"] is True
    resources = await ac.get(
        f"/admin/users/{target.uid}/resources",
        headers=headers,
    )
    assert resources.status_code == status.HTTP_200_OK
    assert resources.json() == []

    suspended = await ac.patch(
        f"/admin/users/{target.uid}/status",
        headers=headers,
        json={"is_active": False},
    )
    assert suspended.status_code == status.HTTP_200_OK
    assert suspended.json()["is_active"] is False
    denied = await ac.get("/user/me", headers=target_headers)
    assert denied.status_code == status.HTTP_401_UNAUTHORIZED

    restored = await ac.patch(
        f"/admin/users/{target.uid}/status",
        headers=headers,
        json={"is_active": True},
    )
    assert restored.status_code == status.HTTP_200_OK
    self_suspend = await ac.patch(
        f"/admin/users/{admin.uid}/status",
        headers=headers,
        json={"is_active": False},
    )
    assert self_suspend.status_code == status.HTTP_409_CONFLICT


@mark_async_test()
async def test_admin_inspects_and_deletes_another_users_resource(
    ac: AsyncClient,
) -> None:
    """影響を確認し、Resource名の一致後だけ既存の退役規則で削除する."""
    expected_sentence_count = 2
    owner = await aregister("resource-owner@example.com")
    _, resource = await save_text(
        owner.uid,
        """
        # 管理削除対象
            保護語: クイズと回答から参照される
            物理削除される単文
        """,
    )
    quiz_uid = uuid4().hex
    answer_uid = uuid4().hex
    rows, _ = await adb.cypher_query(
        """
        MATCH (sentence:Sentence {
            resource_uid: $resource_uid,
            val: 'クイズと回答から参照される'
        })
        CREATE (quiz:Quiz {uid: $quiz_uid})-[:QUIZ_TARGET]->(sentence)
        CREATE (:Answer {uid: $answer_uid})-[:SELECT]->(sentence)
        RETURN sentence.uid
        """,
        params={
            "resource_uid": resource.uid.hex,
            "quiz_uid": quiz_uid,
            "answer_uid": answer_uid,
        },
    )
    protected_uid = rows[0][0]
    headers = await _admin_headers("resource-admin@example.com")

    resources = await ac.get(
        f"/admin/users/{owner.uid}/resources",
        headers=headers,
    )
    assert resources.status_code == status.HTTP_200_OK
    assert resources.json()[0]["sentence_count"] == expected_sentence_count

    impact_response = await ac.get(
        f"/admin/resources/{resource.uid}/deletion-impact",
        headers=headers,
    )
    assert impact_response.status_code == status.HTTP_200_OK
    impact = impact_response.json()
    assert impact["sentence_count"] == expected_sentence_count
    assert impact["term_count"] == 1
    assert impact["quiz_count"] == 1
    assert impact["answer_count"] == 1
    assert impact["retiring_sentence_count"] == 1
    assert impact["deleting_sentence_count"] == 1

    mismatch = await ac.post(
        f"/admin/resources/{resource.uid}/delete",
        headers=headers,
        json={"confirmation": "違う名前"},
    )
    assert mismatch.status_code == status.HTTP_409_CONFLICT
    deleted = await ac.post(
        f"/admin/resources/{resource.uid}/delete",
        headers=headers,
        json={"confirmation": "# 管理削除対象"},
    )
    assert deleted.status_code == status.HTTP_200_OK
    assert deleted.json()["deleted_sentence_count"] == 1
    assert deleted.json()["retired_sentence_count"] == 1
    counts, _ = await adb.cypher_query(
        """
        OPTIONAL MATCH (resource:Resource {uid: $resource_uid})
        OPTIONAL MATCH (active:Sentence {resource_uid: $resource_uid})
        OPTIONAL MATCH (retired:RetiredSentence {uid: $protected_uid})
        RETURN count(DISTINCT resource), count(DISTINCT active),
            count(DISTINCT retired)
        """,
        params={
            "resource_uid": resource.uid.hex,
            "protected_uid": protected_uid,
        },
    )
    assert counts == [[0, 0, 1]]


@mark_async_test()
async def test_admin_resets_regular_user_password(ac: AsyncClient) -> None:
    """adminは通常ユーザーへ新しいパスワードを設定できる."""
    target = await aregister("password-target@example.com")
    headers = await _admin_headers("password-admin@example.com")

    changed = await ac.put(
        f"/admin/users/{target.uid}/password",
        headers=headers,
        json={"password": "new-password"},
    )

    assert changed.status_code == status.HTTP_204_NO_CONTENT
    old_login = await ac.post(
        "/auth/jwt/login",
        data={"username": target.email, "password": "password"},
    )
    new_login = await ac.post(
        "/auth/jwt/login",
        data={"username": target.email, "password": "new-password"},
    )
    assert old_login.status_code == status.HTTP_400_BAD_REQUEST
    assert new_login.status_code == status.HTTP_200_OK


@mark_async_test()
async def test_admin_deletes_regular_user_and_owned_data(ac: AsyncClient) -> None:
    """Resourceの退役規則を通し、通常ユーザーの固有データを削除する."""
    target = await aregister("delete-target@example.com")
    target_uid = to_uuid(target.uid)
    target_headers = await aauth_header(target.email)
    _, resource = await save_text(target.uid, "# 削除ユーザー\n  対象単文\n")
    quiz_uid = uuid4().hex
    answer_uid = uuid4().hex
    await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_uid})
        MATCH (sentence:Sentence {resource_uid: $resource_uid})
        CREATE (user)-[:CREATE]->(quiz:Quiz {
            uid: $quiz_uid,
            quiz_type: 'TERM_TO_SENTENCE'
        })-[:QUIZ_TARGET]->(sentence)
        CREATE (user)-[:ANSWER]->(answer:Answer {uid: $answer_uid})
            -[:ANSWER_OF]->(quiz)
        """,
        params={
            "user_uid": target_uid.hex,
            "resource_uid": resource.uid.hex,
            "quiz_uid": quiz_uid,
            "answer_uid": answer_uid,
        },
    )
    headers = await _admin_headers("delete-admin@example.com")

    mismatch = await ac.post(
        f"/admin/users/{target_uid}/delete",
        headers=headers,
        json={"confirmation": "wrong@example.com"},
    )
    assert mismatch.status_code == status.HTTP_409_CONFLICT
    deleted = await ac.post(
        f"/admin/users/{target_uid}/delete",
        headers=headers,
        json={"confirmation": target.email},
    )

    assert deleted.status_code == status.HTTP_200_OK
    assert deleted.json() == {
        "user_id": str(target_uid),
        "deleted_resource_count": 1,
        "deleted_quiz_count": 1,
        "deleted_answer_count": 1,
    }
    denied = await ac.get("/user/me", headers=target_headers)
    assert denied.status_code == status.HTTP_401_UNAUTHORIZED
    rows, _ = await adb.cypher_query(
        """
        OPTIONAL MATCH (user:User {uid: $user_uid})
        OPTIONAL MATCH (resource:Resource {uid: $resource_uid})
        OPTIONAL MATCH (quiz:Quiz {uid: $quiz_uid})
        OPTIONAL MATCH (answer:Answer {uid: $answer_uid})
        RETURN count(user), count(resource), count(quiz), count(answer)
        """,
        params={
            "user_uid": target_uid.hex,
            "resource_uid": resource.uid.hex,
            "quiz_uid": quiz_uid,
            "answer_uid": answer_uid,
        },
    )
    assert rows == [[0, 0, 0, 0]]
