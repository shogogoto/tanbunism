"""Resource更新時の同一性競合APIテスト."""

from uuid import UUID

from httpx import AsyncClient

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.resource.repo.restore import restore_sysnet
from tanbun.feature.user.testing import async_auth_header


@mark_async_test()
async def test_resource_text_conflict_can_be_resolved_and_retried(
    ac: AsyncClient,
) -> None:
    """409の候補を選んで再送すると旧UIDを選択先へ引き継ぐ."""
    headers = await async_auth_header()
    original = "# identity test\n  abcdef\n"
    created = await ac.post(
        "/resource-text",
        headers=headers,
        json={"txt": original, "path": ["identity.tb"]},
    )
    assert created.is_success
    resource_id = created.json()["resource_id"]
    _old_network, old_uids = await restore_sysnet(resource_id)
    original_uid = old_uids["abcdef"]

    changed = "# identity test\n  abcde1\n  abcde2\n"
    preview = await ac.post(
        "/resource-text/preview",
        headers=headers,
        json={"txt": changed, "path": ["identity.tb"]},
    )
    assert preview.status_code == 409  # noqa: PLR2004

    conflicted = await ac.post(
        "/resource-text",
        headers=headers,
        json={"txt": changed, "path": ["identity.tb"]},
    )

    assert conflicted.status_code == 409  # noqa: PLR2004
    error = conflicted.json().get("detail", conflicted.json())
    assert error["type"] == "identity_conflict"
    assert error["kind"] == "sentence"
    assert error["conflicts"][0]["original"] == "abcdef"

    resolved = await ac.post(
        "/resource-text",
        headers=headers,
        json={
            "txt": changed,
            "path": ["identity.tb"],
            "identity_resolutions": [
                {
                    "kind": "sentence",
                    "original": "abcdef",
                    "replacement": "abcde2",
                },
            ],
        },
    )

    assert resolved.is_success
    _new_network, new_uids = await restore_sysnet(resource_id)
    assert "abcde2" in new_uids, list(new_uids)
    assert new_uids["abcde2"] == original_uid
    assert new_uids["abcde1"] != original_uid

    clean_preview = await ac.post(
        "/resource-text/preview",
        headers=headers,
        json={"txt": changed, "path": ["identity.tb"]},
    )
    assert clean_preview.is_success
    assert clean_preview.json()["sentences_updated"] == 0


@mark_async_test()
async def test_resource_text_rejects_duplicate_resolution_input(
    ac: AsyncClient,
) -> None:
    """同じ旧文に矛盾する選択を送れない."""
    headers = await async_auth_header()
    response = await ac.post(
        "/resource-text",
        headers=headers,
        json={
            "txt": "# identity validation\n  sentence\n",
            "path": ["identity.tb"],
            "identity_resolutions": [
                {"original": "old", "replacement": "new one"},
                {"original": "old", "replacement": "new two"},
            ],
        },
    )

    assert response.status_code == 422  # noqa: PLR2004


@mark_async_test()
async def test_identical_resource_text_has_no_diff(ac: AsyncClient) -> None:
    """同一本文を再送しても変更ありとして扱わない."""
    headers = await async_auth_header()
    text = """# identical preview hash test
  concept: unchanged sentence
"""
    created = await ac.post(
        "/resource-text",
        headers=headers,
        json={"txt": text, "path": ["identical-hash.tb"]},
    )
    assert created.is_success

    preview = await ac.post(
        "/resource-text/preview",
        headers=headers,
        json={"txt": text, "path": ["identical-hash.tb"]},
    )

    assert preview.is_success
    result = preview.json()
    assert UUID(result.pop("resource_id")).hex == created.json()["resource_id"]
    assert result == {
        "is_new": False,
        "sentences_added": 0,
        "sentences_removed": 0,
        "sentences_updated": 0,
        "terms_added": 0,
        "terms_removed": 0,
        "terms_updated": 0,
    }
