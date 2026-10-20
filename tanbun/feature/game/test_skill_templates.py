"""共通設定の参照・保存・交換と、標準行動の編集."""

import json

import pytest
from neomodel import adb
from pydantic import ValidationError

from tanbun.conftest import mark_async_test

from .catalog import (
    GameCatalog,
    SkillSound,
    default_catalog,
    get_game_catalog,
    update_game_catalog,
)
from .scheduled_combat import prepare_scheduled_gambit
from .skill_files import SkillFile, SkillFilePreview
from .skill_outcomes import normal_attack
from .test_scheduled_combat import encounter


def configured_catalog():
    """同じ共通設定を炎・雷で使う."""
    return GameCatalog.model_validate({
        "skill_templates": [
            {
                "id": "spell",
                "name": "初級単体魔法",
                "effect": "attack",
                "damage_type": "special",
                "multiplier": 1.5,
                "mp_cost": 3,
            },
        ],
        "skills": [
            {
                "id": "fire",
                "name": "ファイア",
                "template_id": "spell",
                "element": "fire",
                "animation": "fire",
            },
            {
                "id": "thunder",
                "name": "サンダー",
                "template_id": "spell",
                "element": "lightning",
                "animation": "lightning",
            },
        ],
    })


def test_template_change_updates_all_skills_without_overrides():
    """共通値だけを変更し、個別の名前・属性・演出を維持する."""
    catalog = configured_catalog()
    document = catalog.model_dump()
    document["skill_templates"][0]["multiplier"] = 2
    document["skills"][0]["mp_cost"] = 99
    updated = GameCatalog.model_validate(document)
    assert [s.multiplier for s in updated.skills] == [2, 2]
    assert [s.mp_cost for s in updated.skills] == [3, 3]
    assert [(s.element, s.animation) for s in updated.skills] == [
        ("fire", "fire"),
        ("lightning", "lightning"),
    ]
    snapshot = catalog.skills[0].model_copy(deep=True)
    assert snapshot.multiplier == pytest.approx(1.5)


def test_storage_keeps_references_without_duplicating_values():
    """保存JSONには参照と表示情報だけを保持し、読み込み時に同じ効果へ戻る."""
    catalog = configured_catalog()
    document = catalog.stored_document()
    assert "multiplier" not in document["skills"][0]
    assert "mp_cost" not in document["skills"][0]
    assert GameCatalog.model_validate(document) == catalog
    assert "animation" not in document["skill_templates"][0]
    assert "ailment" not in document["skill_templates"][0]


def test_template_missing_duplicate_and_foreign_fields_are_rejected():
    """未解決参照・重複ID・種類に属さない値は保存できない."""
    document = configured_catalog().stored_document()
    for templates in [
        [],
        [*document["skill_templates"], *document["skill_templates"]],
        [{**document["skill_templates"][0], "ailment": "poison"}],
    ]:
        with pytest.raises(ValidationError):
            GameCatalog.model_validate({**document, "skill_templates": templates})


@pytest.mark.parametrize(
    "patch",
    [
        {"skill_templates": None},
        {"skills": None},
        {"skills": [None]},
    ],
)
def test_malformed_template_documents_are_validation_errors(patch):
    """不正なJSONの構造は500エラーではなく入力エラーにする."""
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(configured_catalog().stored_document() | patch)


def test_v4_file_carries_templates_and_updates_existing_consumers():
    """共通設定を含むJSONを、参照を保って未保存カタログへ統合する."""
    catalog = configured_catalog()
    document = catalog.stored_document()
    document["skill_templates"][0]["mp_cost"] = 5
    file = SkillFile.model_validate({
        "format": "tanbunism.skills",
        "version": 4,
        "skills": document["skills"],
        "skill_templates": document["skill_templates"],
    })
    merged = SkillFilePreview(catalog=catalog, document=file).merged_catalog()
    assert [s.mp_cost for s in merged.skills] == [5, 5]
    assert len(merged.skill_templates) == 1


@mark_async_test()
async def test_saved_template_changes_are_visible_in_live_catalog():
    """DBには参照形式で保存し、adminと実戦で同じ解決済み定義を使う."""
    catalog = default_catalog().model_dump()
    shared = configured_catalog()
    catalog["skills"] = [*catalog["skills"], shared.skills[1]]
    catalog["skill_templates"] = shared.skill_templates
    saved = await update_game_catalog(GameCatalog.model_validate(catalog))
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game_catalog'}) RETURN s.config",
    )
    raw = json.loads(rows[0][0])
    assert "multiplier" not in next(s for s in raw["skills"] if s["id"] == "thunder")
    assert await get_game_catalog() == saved


def test_default_action_uses_catalog_visuals_and_remains_free():
    """MPゼロでも編集した標準行動を使い、固定値へ戻さない."""
    catalog = default_catalog()
    normal = next(s for s in catalog.skills if s.id == "normal_attack")
    normal.animation = "oga_slash_1"
    normal.sound = SkillSound.FIRE
    state, _context = encounter()
    state.save.battle.schedule.actor_id = "player"
    state.save.run.mp = 0
    prepare_scheduled_gambit(state, catalog)
    assert state.save.battle.gambitAction.skill.animation == "oga_slash_1"
    assert state.save.battle.gambitAction.skill.sound == "fire"
    assert normal_attack(catalog).mp_cost == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"mp_cost": 1},
        {"enabled": False},
        {"damage_type": "special"},
        {"on_wrong": {"type": "fizzle"}},
    ],
)
def test_default_action_cannot_stop_being_a_free_risky_single_attack(patch):
    """標準行動の基本条件を壊す変更を拒否する."""
    catalog = default_catalog().model_dump()
    next(s for s in catalog["skills"] if s["id"] == "normal_attack").update(patch)
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(catalog)
