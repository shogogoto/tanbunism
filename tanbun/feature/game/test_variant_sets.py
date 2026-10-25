"""共有する変型セットと、必要な組み合わせだけを保持する参照."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from .catalog import GameCatalog
from .skill_files import SkillFile, SkillFilePreview


def document():
    """段階と範囲を、魔法と物理攻撃で使い回す."""
    return {
        "variant_sets": [
            {
                "id": "tier",
                "name": "威力段階",
                "field": "multiplier",
                "values": [
                    {"id": "low", "name": "初級", "value": 1.2},
                    {"id": "mid", "name": "中級", "value": 1.8},
                    {"id": "high", "name": "上級", "value": 2.5},
                ],
            },
            {
                "id": "range",
                "name": "対象範囲",
                "field": "target_count",
                "values": [
                    {"id": "single", "name": "単体", "value": 1},
                    {"id": "area", "name": "範囲", "value": 3},
                    {"id": "wide", "name": "広域", "value": 5},
                    {"id": "all", "name": "全体", "value": "all"},
                ],
            },
        ],
        "skill_templates": [
            {
                "id": "magic",
                "name": "魔法攻撃",
                "effect": "attack",
                "damage_type": "special",
                "variant_set_ids": ["tier", "range"],
            },
            {
                "id": "slash",
                "name": "斬撃",
                "effect": "attack",
                "variant_set_ids": ["tier", "range"],
            },
            {
                "id": "magic_mid_area",
                "name": "古い名前",
                "base_template_id": "magic",
                "variant_selections": [
                    {"set_id": "range", "value_id": "area"},
                    {"set_id": "tier", "value_id": "mid"},
                ],
            },
            {
                "id": "slash_mid_area",
                "name": "古い名前",
                "base_template_id": "slash",
                "variant_selections": [
                    {"set_id": "tier", "value_id": "mid"},
                    {"set_id": "range", "value_id": "area"},
                ],
            },
        ],
        "skills": [
            {
                "id": "fire",
                "name": "ファイア",
                "template_ids": ["magic_mid_area"],
                "mp_cost": 3,
            },
            {"id": "cut", "name": "斬撃", "template_ids": ["slash_mid_area"]},
        ],
    }


def test_shared_sets_resolve_only_chosen_combinations_and_propagate_changes():
    """三段階と四範囲を展開せず、共有する副名と値を再解決する."""
    raw = document()
    catalog = GameCatalog.model_validate(raw)
    assert len(catalog.skill_templates) == 4  # noqa: PLR2004
    assert catalog.skill_templates[2].name == "魔法攻撃\uff08中級, 範囲\uff09"
    assert catalog.skills[0].damage_type == "special"
    assert catalog.skills[1].damage_type == "physical"
    for skill in catalog.skills:
        assert (skill.multiplier, skill.target_count) == (1.8, 3)
        assert not any(
            key.startswith("variant_") for key in skill.effects[0].model_dump()
        )
    stored = catalog.stored_document()
    assert "multiplier" not in stored["skill_templates"][2]
    assert stored["skill_templates"][2]["override_fields"] == []
    restored = GameCatalog.model_validate(stored)
    assert restored == catalog
    stored["variant_sets"][0]["values"][1].update(name="中位", value=2.2)
    stored["skill_templates"][0]["amount"] = 7
    changed = GameCatalog.model_validate(stored)
    assert all(skill.multiplier == pytest.approx(2.2) for skill in changed.skills)
    assert changed.skills[0].amount == 7  # noqa: PLR2004
    assert changed.skill_templates[2].name == "魔法攻撃\uff08中位, 範囲\uff09"
    file = SkillFile.model_validate({
        "format": "tanbunism.skills",
        "version": 6,
        **{
            key: value
            for key, value in stored.items()
            if key in {"skills", "skill_templates", "variant_sets", "elements"}
        },
    })
    preview = SkillFilePreview(catalog=GameCatalog(), document=file).merged_catalog()
    assert preview.skills[0].multiplier == pytest.approx(2.2)
    assert preview.variant_sets == changed.variant_sets


@pytest.mark.parametrize(
    "case",
    [
        "conflict",
        "missing_set",
        "missing_value",
        "duplicate_selection",
        "duplicate_set",
        "invalid_value",
        "unsupported_field",
    ],
)
def test_rejects_broken_or_conflicting_set_references(case):
    """不正な共有定義を保存して、参照中のスキルを壊さない."""
    raw = deepcopy(document())
    if case == "conflict":
        raw["variant_sets"][1]["field"] = "multiplier"
    elif case == "missing_set":
        raw["variant_sets"].pop()
    elif case == "missing_value":
        raw["variant_sets"][0]["values"].pop(1)
    elif case == "duplicate_selection":
        raw["skill_templates"][2]["variant_selections"] *= 2
    elif case == "duplicate_set":
        raw["variant_sets"].append(raw["variant_sets"][0])
    elif case == "invalid_value":
        raw["variant_sets"][1]["values"][0]["value"] = 99
    else:
        raw["variant_sets"][0]["field"] = "duration_turns"
    with pytest.raises(ValidationError):
        GameCatalog.model_validate(raw)


def test_unused_sets_still_validate_values_and_cannot_smuggle_unknown_fields():
    """使用前のセットでも値や誤答効果の余計な項目を受け入れない."""
    for field, value in [
        ("target_count", 99),
        ("multiplier", -1),
        ("duration_turns", 0),
        ("on_wrong", {"type": "fizzle", "unknown": 1}),
    ]:
        with pytest.raises(ValidationError):
            GameCatalog.model_validate({
                "variant_sets": [
                    {
                        "id": "invalid",
                        "name": "不正",
                        "field": field,
                        "values": [{"id": "bad", "name": "候補", "value": value}],
                    },
                ],
            })
