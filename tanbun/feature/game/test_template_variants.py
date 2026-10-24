"""一段の共通設定と差分だけを持つテンプレートのバリエーション."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test

from .catalog import GameCatalog, get_game_catalog, update_game_catalog
from .skill_files import SkillFile, SkillFilePreview
from .skill_outcomes import skill_effects


def document():
    """同じ魔法を単体・範囲・全体で使う最小のカタログ."""
    return {
        "skill_templates": [
            {
                "id": "spell",
                "name": "初級魔法",
                "effect": "attack",
                "damage_type": "special",
                "multiplier": 1.2,
            },
            {
                "id": "area",
                "name": "範囲",
                "base_template_id": "spell",
                "override_fields": ["target_count"],
                "target_count": 3,
            },
            {
                "id": "all",
                "name": "全体",
                "base_template_id": "spell",
                "override_fields": ["target_count", "multiplier"],
                "target_count": "all",
                "multiplier": 0.8,
            },
        ],
        "skills": [
            {"id": "fire", "name": "ファイア", "template_ids": ["area"], "mp_cost": 3},
        ],
    }


def test_base_changes_propagate_except_explicit_overrides():
    """APIの展開済みの値が古くても、上書きしていない項目は親から再解決する."""
    catalog = GameCatalog.model_validate(document())
    assert [t.multiplier for t in catalog.skill_templates] == [1.2, 1.2, 0.8]
    assert catalog.skills[0].target_count == 3
    frozen = catalog.skills[0].model_copy(deep=True)
    payload = catalog.model_dump()
    payload["skill_templates"][0]["multiplier"] = 2
    updated = GameCatalog.model_validate(payload)
    assert [t.multiplier for t in updated.skill_templates] == [2, 2, 0.8]
    assert updated.skills[0].multiplier == 2
    assert frozen.multiplier == pytest.approx(1.2)
    assert "base_template_id" not in skill_effects(updated.skills[0])[0].model_dump()


def test_storage_and_skill_file_preserve_only_variant_differences():
    """保存・JSON交換後も共通参照と明示的な差分を維持する."""
    catalog = GameCatalog.model_validate(document())
    stored = catalog.stored_document()
    assert stored["skill_templates"][1] == document()["skill_templates"][1]
    assert GameCatalog.model_validate(stored) == catalog
    file = SkillFile.model_validate({
        "format": "tanbunism.skills",
        "version": 5,
        **stored_without_catalog(stored),
    })
    merged = SkillFilePreview(catalog=catalog, document=file).merged_catalog()
    assert merged.stored_document() == stored


def stored_without_catalog(stored):
    """交換ファイルへ利用権限やアイテムは含めない."""
    return {key: stored[key] for key in ("skills", "skill_templates")}


@mark_async_test()
async def test_saved_families_are_resolved_on_read():
    """差分形式で保存した親子が読み込み後も同じ値になり、更新が伝わる."""
    current = (await get_game_catalog()).model_dump()
    family = document()
    current["skill_templates"].extend(family["skill_templates"])
    skills = {skill["id"]: skill for skill in current["skills"]}
    skills.update({skill["id"]: skill for skill in family["skills"]})
    current["skills"] = list(skills.values())
    saved = await update_game_catalog(GameCatalog.model_validate(current))
    assert await get_game_catalog() == saved
    changed = saved.stored_document()
    next(t for t in changed["skill_templates"] if t["id"] == "spell")["multiplier"] = 2
    await update_game_catalog(GameCatalog.model_validate(changed))
    loaded = await get_game_catalog()
    assert next(s for s in loaded.skills if s.id == "fire").multiplier == 2
    assert next(
        t for t in loaded.skill_templates if t.id == "all"
    ).multiplier == pytest.approx(0.8)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "cycle",
        "depth",
        "self",
        "duplicate",
        "unknown",
        "bad_value",
        "no_base",
        "bad_id",
    ],
)
def test_invalid_variants_are_validation_errors(case):
    """多段・循環・参照切れ・範囲外の上書きを500ではなく検証エラーにする."""
    payload = document()
    base, area, all_ = payload["skill_templates"]
    if case == "missing":
        area["base_template_id"] = "missing"
    elif case == "cycle":
        base["base_template_id"] = "area"
    elif case == "depth":
        all_["base_template_id"] = "area"
    elif case == "self":
        area["base_template_id"] = "area"
    elif case == "duplicate":
        area["override_fields"] *= 2
    elif case == "unknown":
        area["override_fields"] = ["mp_cost"]
    elif case == "bad_value":
        all_["multiplier"] = -1
    elif case == "no_base":
        base["override_fields"] = ["target_count"]
    elif case == "bad_id":
        area["id"] = []
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(payload)


def test_child_before_parent_and_reset_to_common_value():
    """表示順と参照解決順は独立。上書きの解除で親の値へ戻す."""
    payload = document()
    payload["skill_templates"].reverse()
    payload["skill_templates"][0]["override_fields"] = ["target_count"]
    catalog = GameCatalog.model_validate(payload)
    assert catalog.skill_templates[0].multiplier == pytest.approx(1.2)
