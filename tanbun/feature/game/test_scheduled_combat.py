"""One action per quiz, backend HP and practice/live scheduling parity."""
# ruff: noqa: PLR2004

from uuid import uuid4

import pytest
from fastapi import HTTPException

from .balance import Allocation, GameBalance
from .catalog import DebuffSkill, EnemyDefinition, GameCatalog
from .combat import CombatContext, EnemyStats, TurnRequest
from .gambits import BattleAction
from .scheduled_combat import (
    begin_scheduled_action,
    initialize_schedule,
    resolve_scheduled,
    scheduled_feedback,
)
from .state import BattleMarker
from .test_practice import source_state


def encounter():
    """Use readable fixture questions but no API or DB writes."""
    state = source_state(uuid4())
    state.save.run.phase = "battle"
    state.save.allocation = Allocation()
    state.save.battle = BattleMarker(id="ctb", region=0, enemies=["e", "e2"])
    context = CombatContext(
        balance=GameBalance(speed_formula="linear"),
        enemies=[
            EnemyStats(
                id=key,
                name=key,
                quizIndex=0,
                quizIndexes=[0, 1],
                hp=100,
                attack=10,
                speed=10,
                relations=0,
                region=0,
            )
            for key in ["e", "e2"]
        ],
    )
    initialize_schedule(state, context)
    return state, context


def answer(state, *, correct=True, **kwargs):
    """Construct a request without any client damage authority."""
    marker = state.save.battle
    target = marker.schedule.quiz_enemy_id
    body = TurnRequest(
        battle_id=marker.id,
        turn=marker.turn,
        answers={target: ["yes"]},
        **kwargs,
    )
    return body, {target: correct}


@pytest.mark.parametrize("actor", ["player", "e"])
def test_correct_answers_benefit_player_for_either_actor(actor):
    """An enemy risky attack can be countered by a correct answer."""
    state, context = encounter()
    state.save.battle.schedule.actor_id = actor
    body, results = answer(state)
    before = state.model_dump_json()
    reply = resolve_scheduled(state, body, results, context, roll_player=lambda *_: 12)
    target = state.save.battle.schedule.quiz_enemy_id
    assert reply["outgoing_damage"] == {target: 12}
    assert reply["state"].save.battle.schedule.enemy_hp[target] == 88
    assert reply["damage"] == 0
    assert reply["state"].save.quizFeedback == "正解"
    assert reply["state"].save.battleFeedback == f"戦う · {target}に12ダメージ"
    restored = type(state).model_validate_json(reply["state"].model_dump_json())
    assert restored.save.quizFeedback == "正解"
    assert restored.save.battleActionDetails == reply["state"].save.battleActionDetails
    assert restored.save.battleActionDetails.targets == (
        ["e"] if actor == "player" else ["あなた"]
    )
    assert "正解" not in restored.save.battleFeedback
    assert state.model_dump_json() == before


@pytest.mark.parametrize("actor", ["player", "e"])
def test_wrong_and_unanswered_risky_attacks_damage_player(actor):
    """Failure progresses combat even on the player's own turn."""
    state, context = encounter()
    state.save.battle.schedule.actor_id = actor
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(state, body, results, context, roll_enemy=lambda *_: 7)
    assert reply["damage"] == 7
    assert reply["state"].save.run.hp == 13
    assert not reply["outgoing_damage"]
    assert reply["state"].save.quizFeedback in {"不正解", "時間切れ"}
    assert reply["state"].save.battleFeedback == "戦う · 7ダメージを受けた"


@pytest.mark.parametrize("retreat", [True, False])
def test_feedback_keeps_events_but_omits_zero_totals(retreat):
    """破損や報酬は残し、ゼロ集計や撤退の重複は出さない."""
    state, context = encounter()
    state.save.battleFeedback = (
        "行動終了 · " + ("撤退 · " if retreat else "") + "武器が壊れた · +10 G"
    )
    body, _ = answer(state, retreat=retreat)
    message = scheduled_feedback(
        state,
        context,
        body,
        outgoing={},
        incoming=0,
    )
    assert message == ("撤退 · " if retreat else "") + "武器が壊れた · +10 G"


def test_feedback_names_defeated_enemy():
    """撃破数の集計ではなく倒した相手を伝える."""
    state, context = encounter()
    state.save.battleFeedback = "行動終了"
    body, _ = answer(state, defeated=["e"])
    assert (
        scheduled_feedback(
            state,
            context,
            body,
            outgoing={"e": 12},
            incoming=0,
        )
        == "eに12ダメージ · eを倒した"
    )


@pytest.mark.parametrize("outcome", ["発動", "不発", "軽減"])
def test_support_feedback_keeps_one_outcome_and_other_events(outcome):
    """支援行動の結果は一度だけ表示し、破損などの付随イベントも残す."""
    state, context = encounter()
    state.save.battleFeedback = f"行動終了 · 回復 · {outcome} · 武器が壊れた"
    body, _ = answer(state)
    assert (
        scheduled_feedback(
            state,
            context,
            body,
            outgoing={},
            incoming=0,
            action_name="回復",
            action_outcome=outcome,
        )
        == f"回復 · {outcome} · 武器が壊れた"
    )


def test_client_cannot_claim_kills_or_answer_other_quizzes():
    """The backend derives kills from its own HP only."""
    state, context = encounter()
    body, results = answer(state, defeated=["e", "e2"])
    reply = resolve_scheduled(state, body, results, context, roll_player=lambda *_: 1)
    assert set(reply["state"].save.battle.enemies) == {"e", "e2"}
    other = "e2" if state.save.battle.schedule.quiz_enemy_id == "e" else "e"
    body.answers[other] = ["yes"]
    with pytest.raises(HTTPException):
        resolve_scheduled(state, body, results, context)


def test_dead_enemy_disappears_and_queue_remains_predictive():
    """Resolved HP survives next-action initialization; dead actors do not."""
    state, context = encounter()
    body, results = answer(state)
    target = state.save.battle.schedule.quiz_enemy_id
    reply = resolve_scheduled(state, body, results, context, roll_player=lambda *_: 100)
    updated = reply["state"]
    assert target not in updated.save.battle.enemies
    assert target not in {
        entry.actor_id for entry in updated.save.battle.schedule.queue
    }
    expected = updated.save.battle.schedule.queue[0].actor_id
    begin_scheduled_action(updated, context)
    assert updated.save.quizFeedback is None
    assert updated.save.battleActionDetails is None
    assert updated.save.battle.schedule.actor_id == expected
    assert updated.save.battle.schedule.enemy_hp[target] == 0


def test_admin_formula_changes_do_not_change_active_encounter():
    """A saved encounter keeps its selected speed rule until completion."""
    state, context = encounter()
    context.balance = GameBalance(speed_formula="sqrt")
    begin_scheduled_action(state, context)
    assert state.save.battle.schedule.formula == "linear"


def test_unanswered_action_is_a_single_risky_failure():
    """Timeout grants no correct answer but still causes one incoming hit."""
    state, context = encounter()
    body, _ = answer(state)
    body.answers = {}
    reply = resolve_scheduled(state, body, {}, context, roll_enemy=lambda *_: 7)
    assert reply["results"] == {}
    assert reply["damage"] == 7
    assert reply["state"].save.run.hp == 13


def test_one_gambit_quiz_can_apply_a_status_to_all_enemies():
    """A direct support skill replaces the normal attack, with one MP charge."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.gambitAction = BattleAction(
        name="全体弱体",
        skill=DebuffSkill(
            id="weaken",
            name="全体弱体",
            target="all_enemies",
            amount=5,
            mp_cost=2,
        ),
    )
    state.save.run.mp = 10
    body, results = answer(state)
    reply = resolve_scheduled(state, body, results, context)
    after = reply["state"]
    assert after.save.run.mp == 8
    assert not reply["outgoing_damage"]
    assert {effect.target_id for effect in after.save.battle.effects} == {"e", "e2"}
    assert after.save.battle.endActionDone


def test_enemy_template_can_override_the_common_speed():
    """An empty enemy speed keeps the balance setting; a value overrides it."""
    from .combat import style_enemies  # noqa: PLC0415

    _, context = encounter()
    enemy = context.enemies[0].model_copy(update={"speed": 12})
    for speed, expected in [(None, 12), (24, 24)]:
        catalog = GameCatalog(
            enemies=[EnemyDefinition(id="ghost", name="敵", speed=speed)],
        )
        assert style_enemies([enemy], catalog)[0].speed == expected
