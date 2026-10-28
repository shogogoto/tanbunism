"""手動取り込み用データを、本番の設定検証と戦闘規則で検査する."""
# ruff: noqa: PLR2004

import json
from pathlib import Path

import pytest

from .balance import GameBalance
from .catalog import default_catalog
from .economy import catalog_products
from .effects import add_effect
from .enemy_gambits import choose_enemy_action, enemy_battle_rules
from .scheduled_combat import begin_scheduled_action, resolve_scheduled
from .settings import BattleSettings
from .settings_files import SettingsSnapshot, merge_settings, settings_preview
from .skill_outcomes import skill_effects
from .test_scheduled_combat import answer, encounter

PACK = Path(__file__).resolve().parents[3] / "docs/game-settings/enemy-variety-v1.json"


def document():
    """起動処理には組み込まない交換データを読む."""
    return json.loads(PACK.read_text())


def merged():
    """既存カタログを保持してデータ案を取り込む."""
    return merge_settings(
        SettingsSnapshot(
            catalog=default_catalog(),
            balance=GameBalance(),
            battle=BattleSettings(),
        ),
        document(),
    )


def prepared(identity):
    """任意の敵役割を、二体の遭遇と同じスナップショットに固定する."""
    catalog = merged().catalog
    definition = next(e for e in catalog.enemies if e.id == identity)
    state, context = encounter()
    actor = context.enemies[0]
    actor.mp = 40
    actor.gambits = enemy_battle_rules(definition, catalog)
    schedule = state.save.battle.schedule
    schedule.enemy_mp[actor.id] = schedule.enemy_max_mp[actor.id] = 40
    return state, context, catalog


def test_pack_is_idempotent_and_changes_only_content_and_named_rewards():
    """数式や初期値、既存のスキル・消費品は変えず、再取り込みで増殖しない."""
    original = SettingsSnapshot(
        catalog=default_catalog(),
        balance=GameBalance(),
        battle=BattleSettings(),
    )
    result = merge_settings(original, document())
    assert len([e for e in result.catalog.enemies if not e.boss]) == 10
    assert len([e for e in result.catalog.enemies if e.boss]) == 3
    assert settings_preview(result, document())["changes"] == []
    assert result.battle == original.battle
    assert result.balance.model_dump(
        exclude={"chest_gold", "chest_healing_potions"},
    ) == original.balance.model_dump(exclude={"chest_gold", "chest_healing_potions"})
    assert (result.balance.chest_gold, result.balance.chest_healing_potions) == (60, 1)
    skills = {s.id: s for s in result.catalog.skills}
    for skill in original.catalog.skills:
        assert skills[skill.id] == skill
    assert {i.id for i in original.catalog.items} <= {
        i.id for i in result.catalog.items
    }
    assert {p.item_id for p in original.catalog.shop if hasattr(p, "item_id")} <= {
        p.item_id for p in result.catalog.shop if hasattr(p, "item_id")
    }


def test_shop_counters_and_limited_direct_skill_unlocks():
    """対策アイテムと対象選択を販売し、直接習得は氷・雷だけ追加する."""
    catalog = merged().catalog
    products = {p.id: p for p in catalog_products(catalog)}
    staff = products["variety_staff"]
    assert staff.price == 180
    assert staff.max_uses == 30
    assert {b.stat for b in staff.equipment_buffs} == {"attack", "specialAttack"}
    assert {b.stat for b in products["variety_shield"].equipment_buffs} == {
        "defense",
        "specialDefense",
    }
    assert products["variety_ice_scroll"].max_uses == 5
    assert products["gambit:variety_enemy_mp_high"].price == 50
    assert products["gambit:variety_self_mp_low"].price == 20
    assert {
        s.skill_id for s in catalog.player_skills if s.skill_id.startswith("variety_")
    } == {"variety_icebolt", "variety_thunderbolt"}


@pytest.mark.parametrize("correct", [True, False])
def test_healer_targets_hurt_ally_and_healing_can_be_prevented(correct):
    """回復役は本人が満タンでも傷ついた仲間を選び、MPを有限に消費する."""
    state, context, _ = prepared("variety_owl_mender")
    marker = state.save.battle
    marker.schedule.enemy_hp = {"e": 100, "e2": 20}
    marker.schedule.waits = {"player": 10, "e": 0, "e2": 10}
    begin_scheduled_action(state, context)
    assert marker.enemyAction.skill.id == "variety_mend"
    assert marker.enemyAction.target_ids == ["e2"]
    body, results = answer(state, correct=correct)
    result = resolve_scheduled(state, body, results, context)[
        "state"
    ].save.battle.schedule
    assert result.enemy_hp["e2"] == (20 if correct else 50)
    assert result.enemy_mp["e"] == 36


@pytest.mark.parametrize(
    ("identity", "status", "skill"),
    [
        ("skull", "variety_guard", "variety_strike"),
        ("variety_orc_mage", "variety_focus", "variety_firebolt"),
        ("bug", "variety_poison", "variety_strike"),
        ("variety_ink_slime", "variety_curse", "variety_strike"),
    ],
)
def test_status_roles_switch_actions_instead_of_reapplying_active_status(
    identity,
    status,
    skill,
):
    """付与済みなら後続行へ進み、MP枯渇時は無料の通常打撃へ戻る."""
    state, context, catalog = prepared(identity)
    marker = state.save.battle
    actor = context.enemies[0]
    state_skill = next(s for s in catalog.skills if s.id == status)
    target = "player" if identity in {"bug", "variety_ink_slime"} else "e"
    for effect in skill_effects(state_skill):
        add_effect(marker, effect, target)
    marker.turn += 1
    assert choose_enemy_action(state.save, actor).skill.id == skill
    marker.schedule.enemy_mp["e"] = 0
    assert choose_enemy_action(state.save, actor).skill.id == "variety_strike"


def test_support_buffs_its_side_not_player_and_switches_when_everyone_is_buffed():
    """味方全員が強化済みなら補助を繰り返さず打撃する."""
    state, context, catalog = prepared("variety_goblin_shaman")
    actor = context.enemies[0]
    selected = choose_enemy_action(state.save, actor)
    assert selected.skill.id == "variety_haste"
    assert "player" not in selected.target_ids
    haste = next(s for s in catalog.skills if s.id == "variety_haste")
    for target in state.save.battle.enemies:
        for effect in skill_effects(haste):
            add_effect(state.save.battle, effect, target)
    state.save.battle.turn += 1
    assert choose_enemy_action(state.save, actor).skill.id == "variety_strike"
