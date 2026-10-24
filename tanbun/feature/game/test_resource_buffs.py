"""HP/MPの能力変更はbuff・debuff・敵味方で同じルールを使う."""
# ruff: noqa: PLR2004

import pytest
from pydantic import ValidationError

from .action_queue import ActionSchedule
from .balance import Allocation, GameBalance
from .catalog import BuffSkill, DebuffSkill, GameCatalog
from .effects import add_effect, sync_bubble
from .inventory import Economy, EquipmentBuff, equipped_stats
from .state import restore_state
from .test_gambits import battle_state


@pytest.mark.parametrize("model", [BuffSkill, DebuffSkill])
@pytest.mark.parametrize("stat", ["maxHp", "maxMp"])
def test_resource_stats_require_percent(model, stat):
    """バフ・デバフで同じHP/MPを選べるが固定値は受け付けない."""
    skill = model(
        id="resource",
        name="能力変更",
        stat=stat,
        amount=50,
        amount_mode="percent",
    )
    assert skill.stat == stat
    with pytest.raises(ValidationError, match="割合"):
        model.model_validate({**skill.model_dump(), "amount_mode": "fixed"})


@pytest.mark.parametrize(("model", "factor"), [(BuffSkill, 1.5), (DebuffSkill, 0.5)])
@pytest.mark.parametrize(("resource", "stat"), [("hp", "maxHp"), ("mp", "maxMp")])
def test_resource_apply_restore_expire_and_battle_end(model, factor, resource, stat):
    """最大値・残量を同率で変更し、再読込で重複適用しない."""
    state = battle_state()
    run, marker = state.save.run, state.save.battle
    run.hp, run.maxHp, run.mp, run.maxMp = 40, 100, 40, 100
    add_effect(
        marker,
        model(
            id="resource",
            name="能力変更",
            stat=stat,
            amount=50,
            amount_mode="percent",
            duration_turns=2,
        ),
        "player",
    )
    marker.turn = 1
    sync_bubble(run, marker)
    assert (getattr(run, resource), getattr(run, stat)) == (40 * factor, 100 * factor)
    restored = restore_state(
        state.model_dump_json(),
        GameBalance(base_hp=100, base_mp=100),
    )
    assert (getattr(restored.save.run, resource), getattr(restored.save.run, stat)) == (
        40 * factor,
        100 * factor,
    )
    setattr(run, resource, int(20 * factor))
    marker.turn = 3
    sync_bubble(run, marker)
    assert (getattr(run, resource), getattr(run, stat)) == (20, 100)
    marker.turn = 1
    sync_bubble(run, marker)
    sync_bubble(run, None)
    assert (getattr(run, resource), getattr(run, stat)) == (20, 100)


@pytest.mark.parametrize(("model", "factor"), [(BuffSkill, 1.5), (DebuffSkill, 0.5)])
def test_enemy_hp_and_mp_use_same_resource_scaling(model, factor):
    """敵の現在値・最大値も割合で変わり、解除で戻る."""
    state = battle_state()
    run, marker = state.save.run, state.save.battle
    marker.schedule = ActionSchedule(
        seed="test",
        enemy_hp={"a": 40},
        enemy_max_hp={"a": 100},
        enemy_mp={"a": 20},
        enemy_max_mp={"a": 100},
    )
    for stat in ("maxHp", "maxMp"):
        add_effect(
            marker,
            model(
                id=stat.lower(),
                name=stat,
                stat=stat,
                amount=50,
                amount_mode="percent",
                duration_turns=2,
            ),
            "a",
        )
    marker.turn = 1
    sync_bubble(run, marker)
    schedule = marker.schedule
    assert schedule.enemy_hp["a"] == 40 * factor
    assert schedule.enemy_mp["a"] == 20 * factor
    assert schedule.enemy_max_hp["a"] == schedule.enemy_max_mp["a"] == 100 * factor
    sync_bubble(run, marker)
    assert schedule.enemy_hp["a"] == 40 * factor
    marker.turn = 3
    sync_bubble(run, marker)
    assert (schedule.enemy_hp["a"], schedule.enemy_mp["a"]) == (40, 20)
    assert schedule.enemy_max_hp["a"] == schedule.enemy_max_mp["a"] == 100


def test_zero_mp_and_large_debuff_do_not_divide_by_zero_or_revive():
    """MP無し・100%以上低下・戦闘不能でも復元できる."""
    state = battle_state()
    run, marker = state.save.run, state.save.battle
    run.hp, run.maxHp, run.mp, run.maxMp = 0, 100, 0, 0
    for stat in ("maxHp", "maxMp"):
        add_effect(
            marker,
            DebuffSkill(
                id=stat.lower(),
                name=stat,
                stat=stat,
                amount=200,
                amount_mode="percent",
            ),
            "player",
        )
    marker.turn = 1
    sync_bubble(run, marker)
    assert (run.hp, run.maxHp, run.mp, run.maxMp) == (0, 1, 0, 0)
    sync_bubble(run, None)
    assert (run.hp, run.maxHp, run.mp, run.maxMp) == (0, 100, 0, 0)


def test_mp_buff_is_usable_as_equipment():
    """アイテムでも同じMPバフを使える."""
    economy = Economy(
        weapon="ring",
        inventory={"ring": 1},
        equipment_buffs=[
            EquipmentBuff(stat="maxMp", amount=100, amount_mode="percent"),
        ],
    )
    assert equipped_stats(Allocation(), GameBalance(base_mp=20), economy)["maxMp"] == 40


@pytest.mark.parametrize("stat", ["maxHp", "maxMp"])
def test_resource_debuff_variant_does_not_revert_to_attack(stat):
    """HP/MPを共有した派生デバフも、そのステータスを維持する."""
    catalog = GameCatalog.model_validate({
        "skill_templates": [
            {
                "id": "base",
                "name": "バフ",
                "effect": "buff",
                "stat": stat,
                "amount": 50,
                "amount_mode": "percent",
            },
            {
                "id": "lower",
                "name": "デバフ",
                "base_template_id": "base",
                "override_fields": ["effect"],
                "effect": "debuff",
            },
        ],
        "skills": [],
    })
    assert catalog.skill_templates[1].effect == "debuff"
    assert catalog.skill_templates[1].stat == stat


def test_repeated_sync_does_not_lose_hp_to_float_rounding():
    """29%等の端数倍率でも再同期のたびにHPが減らない."""
    state = battle_state()
    run, marker = state.save.run, state.save.battle
    run.hp, run.maxHp = 100, 100
    add_effect(
        marker,
        DebuffSkill(
            id="lower",
            name="減少",
            stat="maxHp",
            amount=71,
            amount_mode="percent",
        ),
        "player",
    )
    marker.turn = 1
    sync_bubble(run, marker)
    for _ in range(10):
        sync_bubble(run, marker)
    assert run.hp == run.maxHp == 29
