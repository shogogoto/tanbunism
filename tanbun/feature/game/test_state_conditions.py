"""状態条件はスキル参照だけに統一し、未対応の保存条件は実行しない。."""

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tanbun.conftest import mark_async_test

from .balance import GameBalance
from .catalog import TriggerDefinition, default_catalog, get_game_catalog
from .gambits import GambitRule, choose_action
from .state import restore_state
from .test_gambits import battle_state


@pytest.mark.parametrize("condition", ["buff_present", "buff_absent", "poisoned"])
def test_removed_state_conditions_are_not_supported(condition):
    """旧条件を現行APIの設定値として受け付けない。."""
    with pytest.raises(ValidationError):
        TriggerDefinition(id="state", name="旧条件", condition=condition)
    with pytest.raises(ValidationError):
        GambitRule(condition=condition, definition_id="fire")


def test_state_requires_a_skill_and_missing_skill_never_matches_absence():
    """スキル未指定を「状態なし」と読み替えて実行しない。."""
    with pytest.raises(ValidationError, match="状態系スキル"):
        TriggerDefinition(id="state", name="状態", condition="state")
    state = battle_state()
    state.save.gambits = [
        GambitRule(condition="state", state_present=False, definition_id="fire"),
    ]
    assert choose_action(state.save, default_catalog()) is None


def test_unsupported_saved_rules_do_not_prevent_other_progress_from_loading():
    """未対応の条件だけを除外し、所持品や現行ルールは維持する。."""
    document = battle_state().model_dump()
    document["save"]["gambits"] = [
        {"condition": "poisoned", "definition_id": "fire"},
        {"condition": "state", "definition_id": "fire", "state_present": False},
        {"condition": "always", "definition_id": "fire"},
    ]
    restored = restore_state(json.dumps(document), GameBalance())
    assert [r.condition for r in restored.save.gambits] == ["always"]
    assert restored.save.run.resourceId == document["save"]["run"]["resourceId"]


@mark_async_test()
async def test_catalog_read_discards_unsupported_triggers_not_unrelated_definitions(
    monkeypatch,
):
    """旧トリガーとその敵ルールだけを除外し、スキルや敵そのものを失わない。."""
    from . import catalog as module  # noqa: PLC0415

    raw = default_catalog().stored_document()
    raw["triggers"].append({"id": "old", "name": "旧条件", "condition": "buff_absent"})
    raw["enemies"][0]["gambits"] = [
        {"trigger_id": "old", "skill_id": "fire"},
        {"trigger_id": "always", "skill_id": "fire"},
    ]

    monkeypatch.setattr(
        module.adb,
        "cypher_query",
        AsyncMock(return_value=([[json.dumps(raw)]], [])),
    )
    catalog = await get_game_catalog()
    assert "old" not in {t.id for t in catalog.triggers}
    assert [r.trigger_id for r in catalog.enemies[0].gambits] == ["always"]
    assert [s.id for s in catalog.skills] == [s["id"] for s in raw["skills"]]
    assert [e.id for e in catalog.enemies] == [e["id"] for e in raw["enemies"]]
