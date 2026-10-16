"""優先順に一件だけ選ぶ自動行動。精算は通常クイズと同じ一回の保存に含める。."""

from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .catalog import DefinitionId, GameCatalog, SkillDefinition, get_game_catalog

if TYPE_CHECKING:
    from .state import GameSave


class GambitRule(BaseModel):
    """条件・対象行動・優先順位を表す一行。順序はリストで保持する。."""

    enabled: bool = True
    condition: Literal["always", "hp_below", "mp_below", "enemy_count"] = "always"
    threshold: int = Field(default=30, ge=1, le=100)
    action: Literal["skill", "item"] = "skill"
    definition_id: DefinitionId


class BattleAction(BaseModel):
    """ターン開始時に固定された行動。定義編集や通信再送で変わらない。."""

    skill: SkillDefinition
    item_id: DefinitionId | None = None
    name: str


def choose_action(save: "GameSave", catalog: GameCatalog) -> BattleAction | None:
    """使用不能な条件は飛ばす。回復対象が満タンの場合も次の行へ進む。."""
    run, marker = save.run, save.battle
    if not run or not marker:
        return None
    skills, items = {s.id: s for s in catalog.skills}, {i.id: i for i in catalog.items}
    for rule in save.gambits:
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
        skill = skills.get(item.skill_id if item else rule.definition_id)
        if (
            not skill
            or not skill.enabled
            or skill.effect not in {"attack", "heal_hp", "heal_mp"}
        ):
            continue
        if not can_execute(save, skill, uses_item=item is not None):
            continue
        return BattleAction(
            skill=skill.model_copy(deep=True),
            item_id=item.id if item else None,
            name=item.name if item else skill.name,
        )
    return None


def can_execute(save: "GameSave", skill: SkillDefinition, *, uses_item: bool) -> bool:
    """MP・解放状況・回復先を確認する。."""
    run = save.run
    if not run or run.mp < (0 if uses_item else skill.mp_cost):
        return False
    if not uses_item and not skill.castable:
        return False
    if skill.requires_specialty and not any(save.bossClears.values()):
        return False
    if skill.effect == "heal_hp" and run.hp >= run.maxHp:
        return False
    return not (skill.effect == "heal_mp" and run.mp >= run.maxMp)


def apply_action(
    save: "GameSave",
    *,
    successful: bool,
    retreat: bool = False,
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
        save.economy.inventory[action.item_id] = count - 1
    else:
        if run.mp < skill.mp_cost:
            return None
        run.mp -= skill.mp_cost
    if successful and skill.effect == "heal_hp":
        run.hp = min(run.maxHp, run.hp + skill.amount)
    if successful and skill.effect == "heal_mp":
        run.mp = min(run.maxMp, run.mp + skill.amount)
    return f"{action.name} · {'発動' if successful else '不発'}"


async def prepare_action(save: "GameSave") -> None:
    """ガンビット未設定の既存戦闘には追加の定義読み取りを行わない。."""
    if save.battle:
        save.battle.gambitAction = (
            choose_action(save, await get_game_catalog()) if save.gambits else None
        )


def validate_rules(rules: list[GambitRule], catalog: GameCatalog) -> None:
    """装備や歩数回復を戦闘行動に指定できないよう検査する。."""
    skills, items = {s.id: s for s in catalog.skills}, {i.id: i for i in catalog.items}
    for rule in rules:
        item = items.get(rule.definition_id) if rule.action == "item" else None
        skill = skills.get(item.skill_id if item else rule.definition_id)
        if rule.action == "item" and (not item or item.kind == "weapon"):
            raise HTTPException(422, "戦闘に使えるアイテムを選んでください。")
        if (
            not skill
            or (rule.action == "skill" and not skill.castable)
            or skill.effect not in {"attack", "heal_hp", "heal_mp"}
        ):
            raise HTTPException(422, "戦闘に使えるスキル・アイテムを選んでください。")
