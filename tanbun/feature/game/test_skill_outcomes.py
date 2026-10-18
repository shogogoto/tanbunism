"""カテゴリ別の正誤設定と、一問につき一度だけの効果・消費。."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .catalog import (
    AilmentSkill,
    AttackSkill,
    BuffSkill,
    RecoverySkill,
    ReducedResult,
    parse_skill,
)
from .gambits import BattleAction
from .scheduled_combat import prepare_scheduled_gambit, resolve_scheduled
from .skill_outcomes import answer_effect, normal_attack
from .test_scheduled_combat import answer, encounter


@pytest.mark.parametrize(
    "effect",
    ["heal_hp", "heal_mp", "buff", "debuff", "ailment", "attack"],
)
def test_only_relevant_wrong_answer_fields_are_serialized(effect):
    """不発に効果量、回復に攻撃倍率、状態異常に数値軽減は含めない。."""
    source = {"id": "test", "name": "例", "effect": effect}
    skill = parse_skill(source)
    assert skill.model_dump()["on_wrong"] == {"type": "fizzle"}
    assert parse_skill(skill.model_dump()) == skill
    for wrong in (
        {"type": "fizzle", "amount": 10},
        {"type": "reduced", "apply": True, "amount": 10},
        {"type": "unknown"},
    ):
        with pytest.raises(ValidationError):
            parse_skill(source | {"on_wrong": wrong})
    if effect != "attack":
        with pytest.raises(ValidationError):
            parse_skill(source | {"on_wrong": {"type": "counter"}})


@pytest.mark.parametrize(("correct", "expected"), [(True, 20), (False, 10)])
def test_reduced_recovery_uses_explicit_amount_and_spends_mp_once(correct, expected):
    """割合回復は最大HP基準。誤答でも独立した指定値を使用する。."""
    state, context = encounter()
    state.save.run.hp = 10
    state.save.run.maxHp = 100
    context.balance = context.balance.model_copy(update={"base_hp": 100})
    state.save.run.mp = 10
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.gambitAction = BattleAction(
        name="キュア",
        skill=RecoverySkill(
            id="cure",
            name="キュア",
            effect="heal_hp",
            amount=20,
            amount_mode="percent",
            mp_cost=3,
            on_wrong=ReducedResult(amount=10),
        ),
    )
    body, results = answer(state, correct=correct)
    reply = resolve_scheduled(state, body, results, context)
    assert reply["state"].save.run.hp == 10 + expected
    assert reply["state"].save.run.mp == 7
    assert reply["damage"] == 0
    assert reply["outgoing_damage"] == {}
    assert state.save.run.mp == 10


@pytest.mark.parametrize(
    ("mode", "outgoing", "incoming"),
    [
        ({"type": "fizzle"}, {}, 0),
        ({"type": "counter"}, {}, 7),
        ({"type": "reduced", "amount": 3, "multiplier": 0.5}, {"e": 8, "e2": 8}, 0),
    ],
)
def test_attack_failure_modes_and_target_count(mode, outgoing, incoming):
    """同じ一問で指定人数に効果。範囲反撃でも被ダメージは一体分だけ。."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.gambitAction = BattleAction(
        name="回転切り",
        skill=AttackSkill(
            id="spin",
            name="回転切り",
            target_count=3,
            multiplier=2,
            on_wrong=mode,
        ),
    )
    body, results = answer(state, correct=False)
    reply = resolve_scheduled(
        state,
        body,
        results,
        context,
        roll_player=lambda *_: 10,
        roll_enemy=lambda *_: 7,
    )
    assert reply["outgoing_damage"] == outgoing
    assert reply["damage"] == incoming


@pytest.mark.parametrize(("kind", "expected"), [("physical", 15), ("special", 38)])
def test_attack_categories_use_separate_attack_and_defense(kind, expected):
    """物理と特殊で実際に別の能力を参照する。."""
    state, context = encounter()
    context.balance = context.balance.model_copy(
        update={
            "base_attack": 20,
            "base_special_attack": 40,
        },
    )
    for enemy in context.enemies:
        enemy.defense, enemy.special_defense = 5, 2
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    marker.gambitAction = BattleAction(
        name="技",
        skill=AttackSkill(
            id="hit",
            name="技",
            damage_type=kind,
        ),
    )
    body, results = answer(state)
    reply = resolve_scheduled(
        state,
        body,
        results,
        context,
        roll_enemy=lambda attack, defense, _: attack - defense,
    )
    assert reply["outgoing_damage"] == {"e": expected}


@pytest.mark.parametrize(("correct", "expected"), [(True, 5), (False, 10)])
def test_enemy_reduced_attack_inverts_quiz_success(correct, expected):
    """敵が軽減攻撃するときも、正答はプレイヤーに有利。."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "e"
    marker.schedule.quiz_enemy_id = "e"
    context.enemies[0].attack_skill = AttackSkill(
        id="magic",
        name="魔法",
        on_wrong={"type": "reduced", "amount": 0, "multiplier": 0.5},
    )
    body, results = answer(state, correct=correct)
    reply = resolve_scheduled(state, body, results, context, roll_enemy=lambda *_: 10)
    assert reply["damage"] == expected
    assert not reply["outgoing_damage"]


def test_reduced_status_keeps_duration_and_binary_ailment_choice():
    """能力変化は量だけ、状態異常は付与の有無だけを変更する。."""
    buff = BuffSkill(
        id="buff",
        name="強化",
        amount=20,
        duration_turns=4,
        on_wrong={"type": "reduced", "amount": 5},
    )
    effect = answer_effect(buff, correct=False)
    assert (effect.amount, effect.duration_turns) == (5, 4)
    for apply in (True, False):
        poison = AilmentSkill(
            id="poison",
            name="毒",
            on_wrong={"type": "reduced", "apply": apply},
        )
        assert bool(answer_effect(poison, correct=False)) is apply


def test_fallback_is_free_counter_attack_when_no_gambit_can_execute():
    """デフォルト行動は未設定・MP不足でも常に最下位で使用できる。."""
    from .catalog import default_catalog  # noqa: PLC0415

    state, _ = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = "player"
    state.save.run.mp = 0
    prepare_scheduled_gambit(state, default_catalog())
    assert marker.gambitAction.skill == normal_attack()
    assert marker.gambitAction.skill.mp_cost == 0
    assert marker.gambitAction.skill.on_wrong.type == "counter"


def test_specialty_counter_failure_applies_once_and_preserves_moves_on_defeat():
    """専用クイズの反撃も、出題時に固定した一体分のダメージを使う。."""
    from .end_actions import finish_action  # noqa: PLC0415

    state, _ = encounter()
    marker = state.save.battle
    state.save.run.moves = 3
    marker.endAction = {
        "id": "specialty",
        "name": "斬撃",
        "skill": normal_attack().model_dump(),
        "quiz_ids": ["one"],
        "index": 1,
        "correct": 0,
        "counter_damage": 100,
        "enemy_hp": {"e": 100, "e2": 100},
    }
    assert finish_action(state) == {"e": 0}
    assert state.save.run.hp == 0
    assert state.save.run.phase == "defeated"
    assert state.save.run.moves == 3
    assert state.save.battle is None
    assert state.save.endActionReceipt["damage"] == 100


def test_legacy_specialty_ratio_migrates_without_overwriting_explicit_outcomes():
    """旧利用設定の誤答倍率を一度だけ量へ移し、以後はスキルを正とする。."""
    from .catalog import GameCatalog  # noqa: PLC0415

    source = {
        "skills": [{"id": "heal", "name": "回復", "effect": "heal_hp", "amount": 20}],
        "specialty_skills": [{"skill_id": "heal", "wrong_multiplier": 0.5}],
    }
    catalog = GameCatalog.model_validate(source)
    assert catalog.skills[0].on_wrong.model_dump() == {"type": "reduced", "amount": 10}
    restored = catalog.model_dump()
    restored["skills"][0]["on_wrong"] = {"type": "fizzle"}
    assert GameCatalog.model_validate(restored).skills[0].on_wrong.type == "fizzle"


@pytest.mark.parametrize(("actor", "expected"), [("player", False), ("e", True)])
def test_on_hit_ailments_follow_actual_hits_not_quiz_failure(actor, expected):
    """不発には敵の命中効果なし。敵の軽減が命中したら正答でも付与する。."""
    state, context = encounter()
    marker = state.save.battle
    marker.schedule.actor_id = actor
    marker.schedule.quiz_enemy_id = "e"
    context.enemies[0].on_hit_skill = AilmentSkill(id="poison", name="毒")
    if actor == "player":
        marker.gambitAction = BattleAction(
            skill=AttackSkill(id="fail", name="不発"),
            name="不発",
        )
    else:
        context.enemies[0].attack_skill = AttackSkill(
            id="reduced",
            name="軽減",
            on_wrong={"type": "reduced", "amount": 0, "multiplier": 0.5},
        )
    body, results = answer(state, correct=actor == "e")
    reply = resolve_scheduled(state, body, results, context, roll_enemy=lambda *_: 10)
    assert bool(reply["state"].save.battle.effects) is expected
