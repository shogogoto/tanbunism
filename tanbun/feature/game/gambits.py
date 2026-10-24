"""ターン末に優先順で一件だけ選ぶ自動行動と、旧形式の精算互換処理。."""

from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .catalog import (
    DefinitionId,
    GameCatalog,
    SkillDefinition,
    SkillTarget,
    SpecialtySkillUse,
    StoredSkill,
)
from .effects import add_effect, apply_resource_effect
from .inventory import consume_use
from .skill_outcomes import answer_effect, skill_effects, skill_targets

if TYPE_CHECKING:
    from .state import GameSave


class GambitRule(BaseModel):
    """条件・対象行動・優先順位を表す一行。順序はリストで保持する。."""

    enabled: bool = True
    condition: Literal[
        "always",
        "hp_below",
        "mp_below",
        "enemy_count",
        "buff_present",
        "buff_absent",
        "poisoned",
    ] = "always"
    threshold: int = Field(default=30, ge=1, le=100)
    action: Literal["skill", "item", "specialty"] = "skill"
    definition_id: DefinitionId
    trigger_id: DefinitionId | None = None
    target: Literal["self", "enemy"] | None = None


class BattleAction(BaseModel):
    """選択時に固定された行動。定義編集や通信再送で変わらない。."""

    skill: StoredSkill
    item_id: DefinitionId | None = None
    name: str
    specialty_id: str | None = None
    specialty_use: SpecialtySkillUse | None = None
    item_max_uses: int = Field(default=1, ge=1, le=10000)


def choose_action(
    save: "GameSave",
    catalog: GameCatalog,
    *,
    slots: int = 10,
) -> BattleAction | None:
    """使用不能な条件は飛ばす。回復対象が満タンの場合も次の行へ進む。."""
    run, marker = save.run, save.battle
    if not run or not marker:
        return None
    skills, items = {s.id: s for s in catalog.skills}, {i.id: i for i in catalog.items}
    for stored_rule in save.gambits[:slots]:
        rule = effective_rule(stored_rule, catalog)
        if rule is None:
            continue
        matches = (
            rule.condition == "always"
            or (
                rule.condition == "hp_below"
                and run.hp * 100 <= run.maxHp * rule.threshold
            )
            or (
                rule.condition == "mp_below"
                and run.mp * 100 <= run.maxMp * rule.threshold
            )
            or (
                rule.condition == "enemy_count"
                and len(marker.enemies) >= rule.threshold
            )
            or (
                rule.condition in {"buff_present", "buff_absent"}
                and (
                    any(
                        e.target_id == "player"
                        and e.skill.effect == "buff"
                        and e.starts_turn <= marker.turn < e.expires_turn
                        for e in marker.effects
                    )
                    == (rule.condition == "buff_present")
                )
            )
            or (
                rule.condition == "poisoned"
                and any(
                    e.target_id == "player"
                    and e.skill.effect == "ailment"
                    and e.skill.ailment == "poison"
                    and e.starts_turn <= marker.turn < e.expires_turn
                    for e in marker.effects
                )
            )
        )
        if not rule.enabled or not matches:
            continue
        item = items.get(rule.definition_id) if rule.action == "item" else None
        if rule.action == "item" and (
            not item
            or not item.enabled
            or item.kind == "weapon"
            or save.economy.inventory.get(item.id, 0) < 1
        ):
            continue
        specialty = (
            next(
                (
                    s
                    for s in save.specialties
                    if s.id == rule.definition_id and s.equipped
                ),
                None,
            )
            if rule.action == "specialty"
            else None
        )
        if rule.action == "specialty" and not specialty:
            continue
        skill = skills.get(
            specialty.skill_id
            if specialty
            else item.skill_id
            if item
            else rule.definition_id,
        )
        if (
            not skill
            or not skill.enabled
            or skill.effect
            not in {
                "attack",
                "heal_hp",
                "heal_mp",
                "damage_mp",
                "buff",
                "debuff",
                "ailment",
            }
        ):
            continue
        selected_skill = retarget_skill(skill, rule.target)
        if not can_execute(save, selected_skill, uses_item=item is not None):
            continue
        use = next(
            (
                u
                for u in catalog.specialty_skills
                if u.skill_id == skill.id and u.enabled
            ),
            None,
        )
        if (specialty and not use) or (
            rule.action == "skill"
            and not any(
                u.skill_id == skill.id and u.enabled for u in catalog.player_skills
            )
        ):
            continue
        return BattleAction(
            skill=selected_skill,
            item_id=item.id if item else None,
            name=item.name if item else skill.name,
            specialty_id=specialty.id if specialty else None,
            specialty_use=use if specialty else None,
            item_max_uses=item.max_uses if item else 1,
        )
    return None


def retarget_skill(skill, target):
    """共通定義は変えず、行動スナップショットだけへ対象を固定する."""
    selected = skill.model_copy(deep=True)
    if target:
        selected.target = SkillTarget(target)
        for effect in selected.effects:
            effect.target = SkillTarget(target)
    return selected


def can_execute(save: "GameSave", skill: SkillDefinition, *, uses_item: bool) -> bool:
    """MP・解放状況・回復先を確認する。."""
    run = save.run
    if not run or run.mp < (0 if uses_item else skill.mp_cost):
        return False
    return any(
        (effect.target != "self" or effect.effect != "heal_hp" or run.hp < run.maxHp)
        and (
            effect.target != "self" or effect.effect != "heal_mp" or run.mp < run.maxMp
        )
        for effect in skill_effects(skill)
    )


def apply_action(
    save: "GameSave",
    *,
    successful: bool,
    retreat: bool = False,
    results: dict[str, bool] | None = None,
    context=None,
) -> str | None:
    """消費は発動時一回。通常クイズが正解なら、敵の反撃前に回復する。."""
    run, marker = save.run, save.battle
    action = marker.gambitAction if marker else None
    if not action or not run or retreat:
        return None
    skill = action.skill
    if action.item_id:
        count = save.economy.inventory.get(action.item_id, 0)
        if count < 1:
            return None
        consume_use(save.economy, action.item_id, action.item_max_uses)
    else:
        if run.mp < skill.mp_cost:
            return None
        run.mp -= skill.mp_cost
    applied = False
    for component in skill_effects(skill):
        effect = answer_effect(component, successful)
        applied = applied or effect is not None
        if effect:
            apply_resource_effect(save, effect, context=context)
        apply_status_effects(
            marker,
            component,
            successful=successful,
            results=results or {},
        )
    label = (
        "発動"
        if successful
        else "軽減"
        if applied
        else (
            "反撃"
            if any(
                getattr(s, "on_wrong", None) and s.on_wrong.type == "counter"
                for s in skill_effects(skill)
            )
            else "不発"
        )
    )
    return f"{action.name} · {label}"


def apply_status_effects(
    marker,
    skill: SkillDefinition,
    *,
    successful: bool,
    results: dict[str, bool],
) -> None:
    """正解対象にだけ能力変化を付与する."""
    effect = answer_effect(skill, successful)
    if (
        effect
        and skill.effect in {"buff", "debuff", "ailment"}
        and skill.target == "self"
    ):
        add_effect(marker, effect, "player")
    if skill.effect in {"buff", "debuff", "ailment"} and skill.target != "self":
        targets = skill_targets(skill, marker.enemies)
        for target in targets:
            applied = (
                effect
                if marker.schedule
                else answer_effect(skill, bool(results.get(target)))
            )
            if applied:
                add_effect(marker, applied, target)


async def prepare_action(save: "GameSave", user_id) -> None:  # noqa: RUF029
    """ガンビット未設定の既存戦闘には追加の定義読み取りを行わない。."""
    if save.battle:
        save.battle.effects = [
            e for e in save.battle.effects if e.expires_turn > save.battle.turn
        ]
        # Selection belongs to the end of the turn, after ordinary damage.
        save.battle.gambitAction = None


def effective_rule(rule: GambitRule, catalog: GameCatalog) -> GambitRule | None:
    """削除・無効化された条件テンプレートを実行しない."""
    if not rule.trigger_id:
        return rule
    trigger = next(
        (t for t in catalog.triggers if t.id == rule.trigger_id and t.enabled),
        None,
    )
    return rule.model_copy(update={"condition": trigger.condition}) if trigger else None


def validate_rules(rules: list[GambitRule], catalog: GameCatalog) -> None:
    """装備や歩数回復を戦闘行動に指定できないよう検査する。."""
    skills, items = {s.id: s for s in catalog.skills}, {i.id: i for i in catalog.items}
    for rule in rules:
        if rule.action == "specialty":
            continue  # Owned/equipped references are checked at execution time.
        item = items.get(rule.definition_id) if rule.action == "item" else None
        skill = skills.get(item.skill_id if item else rule.definition_id)
        if rule.action == "item" and (not item or item.kind == "weapon"):
            raise HTTPException(422, "戦闘に使えるアイテムを選んでください。")
        direct_locked = (
            skill
            and rule.action == "skill"
            and not any(
                u.skill_id == skill.id and u.enabled for u in catalog.player_skills
            )
        )
        if (
            not skill
            or direct_locked
            or skill.effect
            not in {
                "attack",
                "heal_hp",
                "heal_mp",
                "damage_mp",
                "buff",
                "debuff",
                "ailment",
            }
        ):
            raise HTTPException(422, "戦闘に使えるスキル・アイテムを選んでください。")
