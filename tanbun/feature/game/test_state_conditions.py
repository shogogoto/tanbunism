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


def test_unsupported_saved_rules_are_rejected_without_silent_repair():
    """不正な保存条件を黙って削除せず、型の検証エラーとして扱う."""
    document = battle_state().model_dump()
    document["save"]["gambits"] = [
        {"condition": "poisoned", "definition_id": "fire"},
        {"condition": "state", "definition_id": "fire", "state_present": False},
        {"condition": "always", "definition_id": "fire"},
    ]
    with pytest.raises(ValidationError):
        restore_state(json.dumps(document), GameBalance())


@mark_async_test()
async def test_catalog_read_rejects_unsupported_triggers_without_writing(
    monkeypatch,
):
    """廃止した条件を黙って削除せず、保存済みの不正な定義として知らせる."""
    from . import catalog_repo as module  # noqa: PLC0415

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
    with pytest.raises(ValidationError):
        await get_game_catalog()
    module.adb.cypher_query.assert_awaited_once()
