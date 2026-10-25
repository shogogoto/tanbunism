"""スキル交換ファイルの互換性・参照整合性・非保存プレビュー."""
# ruff: noqa: PLR2004

import json

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .catalog import GameCatalog, SkillEffect, SkillSound, default_catalog
from .gambits import BattleAction
from .skill_files import SkillFile, SkillFilePreview


def document(skills):
    """バージョン付きの交換形式."""
    return {"format": "tanbunism.skills", "version": 1, "skills": skills}


@pytest.mark.parametrize(
    "animation",
    [
        "willo_ice_shatter",
        "willo_zap",
        "willo_fire_impact",
        "willo_cast",
        "willo_heal",
        "willo_impact",
        "oga_star_ice",
        "oga_star_thunder",
        "oga_star_fire",
    ],
)
def test_new_spell_materials_survive_catalog_and_skill_file(animation):
    """映像の新IDとそのSEを保存・JSON再読込で失わない."""
    raw = {
        "id": "spell",
        "name": "魔法",
        "effect": "attack",
        "animation": animation,
        "sound": "willo_zap",
    }
    catalog = GameCatalog.model_validate({"skills": [raw]})
    restored = GameCatalog.model_validate(catalog.stored_document())
    assert restored.skills[0].animation == animation
    file = SkillFile.model_validate({
        "format": "tanbunism.skills",
        "version": 6,
        "skills": [raw],
    })
    assert file.skills[0].animation == animation
    assert file.skills[0].sound == "willo_zap"


def test_sound_roundtrip_defaults_and_unknown_preset_validation():
    """旧定義は自動SE、指定した音はJSONにも残り、不明な音は拒否する."""
    definition = {"id": "fire", "name": "炎", "effect": "attack"}
    legacy = SkillFile.model_validate(document([definition]))
    assert legacy.skills[0].sound == "auto"
    selected = SkillFile.model_validate(document([{**definition, "sound": "fire"}]))
    assert json.loads(selected.model_dump_json())["skills"][0]["sound"] == "fire"
    with pytest.raises(ValidationError):
        SkillFile.model_validate(document([{**definition, "sound": "missing"}]))


@pytest.mark.parametrize("sound", list(SkillSound))
def test_all_sound_materials_can_be_saved_and_exported(sound):
    """演出パネルで選べる音声素材を、カタログと交換JSONで保持できる."""
    definition = {"id": "effect", "name": "演出", "effect": "attack", "sound": sound}
    file = SkillFile.model_validate({**document([definition]), "version": 2})
    exported = json.loads(file.model_dump_json())
    assert exported["skills"][0]["sound"] == sound.value
    assert SkillFile.model_validate(exported).skills[0].sound == sound


@pytest.mark.parametrize(
    ("effect", "specific"),
    [
        (
            "attack",
            {"amount_mode", "multiplier", "damage_type", "target_count", "on_wrong"},
        ),
        ("heal_hp", {"amount_mode", "on_wrong"}),
        ("heal_mp", {"amount_mode", "on_wrong"}),
        ("buff", {"amount_mode", "stat", "duration_turns", "stack_limit", "on_wrong"}),
        (
            "debuff",
            {
                "amount_mode",
                "stat",
                "duration_turns",
                "stack_limit",
                "target_count",
                "on_wrong",
            },
        ),
        (
            "ailment",
            {
                "amount_mode",
                "ailment",
                "duration_turns",
                "stack_limit",
                "target_count",
                "on_wrong",
            },
        ),
        ("recover_moves", set()),
        ("equip_attack", set()),
    ],
)
def test_v2_serializes_only_fields_belonging_to_its_effect(effect, specific):
    """攻撃に毒、回復に攻撃倍率など、無関係な項目は定義にも出力にもない."""
    file = SkillFile.model_validate({
        **document([{"id": "example", "name": "例", "effect": effect}]),
        "version": 2,
    })
    base = {
        "id",
        "name",
        "icon",
        "enabled",
        "mp_cost",
        "animation",
        "sound",
        "effect",
        "target",
        "amount",
        "template_id",
        "template_ids",
        "effects",
        "element",
        "target_count",
        "damage_type",
    }
    skill = file.skills[0]
    conditional = {"affected_element"} if effect in {"buff", "debuff"} else set()
    assert type(skill).model_fields.keys() == base | specific | conditional
    assert json.loads(file.model_dump_json())["skills"][0].keys() == base | specific


@pytest.mark.parametrize(
    ("effect", "foreign"),
    [
        ("attack", {"ailment": "poison"}),
        ("attack", {"duration_turns": 3}),
        ("heal_hp", {"multiplier": 2}),
        ("buff", {"ailment": "poison"}),
        ("ailment", {"stat": "attack"}),
        ("recover_moves", {"amount_mode": "percent"}),
    ],
)
def test_v2_rejects_keys_from_other_effects(effect, foreign):
    """新形式では不要なキーを黙って消さず、入力間違いとして知らせる."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        SkillFile.model_validate({
            **document([{"id": "example", "name": "例", "effect": effect, **foreign}]),
            "version": 2,
        })


def test_v1_catalog_and_in_progress_actions_migrate_flat_fields():
    """旧ファイル・保存済み定義・進行中の戦闘を同じ効果のまま復元する."""
    legacy = default_catalog().skills[0].model_dump(mode="json") | {
        "stat": "attack",
        "duration_turns": 3,
        "stack_limit": 1,
        "ailment": "poison",
    }
    imported = SkillFile.model_validate(document([legacy])).skills[0]
    old = default_catalog().model_dump(mode="json")
    old["skills"][0] = legacy
    restored = GameCatalog.model_validate(old)
    action = BattleAction.model_validate({"name": "攻撃", "skill": legacy})
    for skill in (imported, restored.skills[0], action.skill):
        assert skill.multiplier == pytest.approx(1.5)
        assert (
            not {"stat", "duration_turns", "stack_limit", "ailment"}
            & skill.model_dump().keys()
        )


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
    assert set(
        restored.model_json_schema()["properties"]["skills"]["items"]["discriminator"][
            "mapping"
        ],
    ) == {effect.value for effect in SkillEffect}


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
        {"version": 999},
        {"format": "other"},
        {"skills": [{"id": "wrong", "name": "誤記", "effect": "unknown"}]},
        {"skills": [{"id": "wrong", "name": "誤記", "effect": "attack", "mpcost": 3}]},
        {"skills": [{"id": "wrong", "name": "誤記", "effect": "attack", "amount": -1}]},
        {
            "skills": [
                {
                    "id": "wrong",
                    "name": "誤記",
                    "effect": "attack",
                    "target": "unknown",
                },
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
    item = next(item for item in catalog.items if item.kind == "weapon")
    weapon = next(skill for skill in catalog.skills if skill.id == item.skill_id)
    preview = SkillFilePreview(
        catalog=catalog,
        document=document([
            {"id": weapon.id, "name": weapon.name, "effect": "heal_hp"},
        ]),
    )
    with pytest.raises(ValidationError, match="スキルまたは種類"):
        preview.merged_catalog()


def test_shared_equipment_buff_can_be_imported_without_changing_item_references():
    """装備用の共通バフを更新しても、アイテムの参照や種類は維持する."""
    catalog = default_catalog()
    item = next(item for item in catalog.items if item.kind == "weapon")
    skill = next(skill for skill in catalog.skills if skill.id == item.skill_id)
    assert skill.effect == "buff"
    changed = skill.model_dump() | {"amount": skill.amount + 1}
    merged = SkillFilePreview(
        catalog=catalog,
        document=document([changed]),
    ).merged_catalog()
    imported = next(skill for skill in merged.skills if skill.id == item.skill_id)
    assert imported.effect == "buff"
    assert imported.amount == changed["amount"]
    assert merged.items == catalog.items
    assert next(skill for skill in catalog.skills if skill.id == item.skill_id) == skill


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
