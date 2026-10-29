"""候補全員ではなく、行動時に選んだ発動条件と対象を表示する."""

import pytest

from .action_details import describe_action
from .catalog import AttackSkill
from .gambits import BattleAction
from .test_scheduled_combat import encounter


@pytest.mark.parametrize(("count", "expected"), [(1, 1), (3, 3), (5, 5), ("all", 6)])
def test_details_limit_ranked_candidates_to_actual_target_count(count, expected):
    """順位付き候補を全員表示せず、1・3・5・全体に絞った対象を固定する."""
    state, _ = encounter()
    marker = state.save.battle
    marker.enemies = [f"e{i}" for i in range(6)]
    candidates = list(reversed(marker.enemies))
    skill = AttackSkill(
        id="spell",
        name="魔法",
        effect="attack",
        target="enemy",
        target_count=count,
    )
    action = BattleAction(
        skill=skill,
        name=skill.name,
        target_ids=candidates,
        trigger_name="守が低い敵",
    )
    names = {identity: f"敵{i}" for i, identity in enumerate(candidates)}
    details = describe_action(action, skill, marker, names)
    assert details.trigger == "守が低い敵"
    assert details.targets == [names[identity] for identity in candidates[:expected]]


def test_default_attack_details_identify_the_implicit_trigger():
    """最下位の通常攻撃も、ユーザー設定のトリガーと区別できる."""
    state, _ = encounter()
    skill = AttackSkill(id="strike", name="戦う", effect="attack", target="enemy")
    details = describe_action(
        None,
        skill,
        state.save.battle,
        {"player": "あなた", "e": "ゴースト"},
    )
    assert details.trigger == "いつでも（通常攻撃）"  # noqa: RUF001
    assert details.targets == ["ゴースト"]
