"""スキル交換ファイルの互換性・参照整合性・非保存プレビュー."""
# ruff: noqa: PLR2004

import json

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .catalog import SkillEffect, default_catalog
from .skill_files import SkillFile, SkillFilePreview


def document(skills):
    """バージョン付きの交換形式."""
    return {"format": "tanbunism.skills", "version": 1, "skills": skills}


def test_str_enums_preserve_existing_json_values_and_round_trip():
    """既存の文字列定義を移行なしで読み、同じ文字列で書き出せる."""
    catalog = default_catalog()
    payload = json.loads(catalog.model_dump_json())
    assert payload["skills"][0]["effect"] == "attack"
    assert catalog.skills[0].effect is SkillEffect.ATTACK
    restored = SkillFile.model_validate(document(payload["skills"]))
    assert [s.model_dump() for s in restored.skills] == [
        s.model_dump() for s in catalog.skills
    ]
    assert restored.model_json_schema()["$defs"]["SkillEffect"]["enum"] == [
        effect.value for effect in SkillEffect
    ]


def test_import_updates_by_id_appends_new_and_preserves_consumers():
    """未掲載の定義・利用権限・所持品・ショップは書き換えない."""
    catalog = default_catalog()
    existing = catalog.model_dump_json()
    skill = catalog.skills[0].model_dump() | {"amount": 7}
    preview = SkillFilePreview(
        catalog=catalog,
        document=document([
            skill,
            {"id": "new_skill", "name": "新技", "effect": "attack"},
        ]),
    )
    merged = preview.merged_catalog()
    assert merged.skills[0].amount == 7
    assert merged.skills[1:-1] == catalog.skills[1:]
    assert merged.skills[-1].id == "new_skill"
    assert merged.player_skills == catalog.player_skills
    assert merged.specialty_skills == catalog.specialty_skills
    assert merged.items == catalog.items
    assert merged.shop == catalog.shop
    assert merged.enemies == catalog.enemies
    assert catalog.model_dump_json() == existing


@pytest.mark.parametrize(
    "patch",
    [
        {"version": 2},
        {"format": "other"},
        {"skills": [{"id": "wrong", "name": "誤記", "effect": "unknown"}]},
        {"skills": [{"id": "wrong", "name": "誤記", "effect": "attack", "mpcost": 3}]},
        {"skills": [{"id": "wrong", "name": "誤記", "effect": "attack", "amount": -1}]},
        {
            "skills": [
                {"id": "wrong", "name": "誤記", "effect": "attack", "target": "self"},
            ],
        },
    ],
)
def test_invalid_files_are_rejected_without_partial_import(patch):
    """バージョン・誤記・範囲・対象の誤りを読み飛ばさない."""
    with pytest.raises(ValidationError):
        SkillFile.model_validate(document([]) | patch)


def test_duplicate_ids_and_broken_item_references_are_rejected():
    """重複IDと装備種別を壊すスキル変更は取り込めない."""
    catalog = default_catalog()
    skill = catalog.skills[0].model_dump()
    with pytest.raises(ValidationError, match="重複"):
        SkillFile.model_validate(document([skill, skill]))
    weapon = next(s for s in catalog.skills if s.effect == "equip_attack")
    preview = SkillFilePreview(
        catalog=catalog,
        document=document([weapon.model_dump() | {"effect": "heal_hp"}]),
    )
    with pytest.raises(ValidationError, match="スキルまたは種類"):
        preview.merged_catalog()


@mark_async_test()
async def test_preview_requires_admin_and_does_not_save(ac):
    """管理者の検証でも、明示的な保存前には共通カタログを変えない."""
    user = await aregister("skill-files@example.com")
    headers = await aauth_header(user.email)
    catalog = default_catalog().model_dump(mode="json")
    payload = {
        "catalog": catalog,
        "document": document([catalog["skills"][0] | {"amount": 7}]),
    }
    path = "/admin/game/skills/preview"
    assert (await ac.post(path, json=payload)).status_code == 401
    assert (await ac.post(path, json=payload, headers=headers)).status_code == 403
    user.is_superuser = True
    await user.save()
    response = await ac.post(path, json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["skills"][0]["amount"] == 7
    assert (await ac.get("/admin/game/catalog", headers=headers)).json() == catalog
    payload["document"]["skills"][0]["effect"] = "invalid"
    assert (await ac.post(path, json=payload, headers=headers)).status_code == 422
