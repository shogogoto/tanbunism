"""敵陣営から見た条件・対象を使い、行動開始時に選択を固定する。."""

from .catalog import StoredSkill, TriggerCondition, TriggerTarget
from .gambits import BattleAction, can_receive, condition_matches, retarget_skill


class EnemyBattleRule(TriggerCondition):
    """遭遇時に固定した敵の条件とスキル。."""

    target: TriggerTarget = "enemy"
    skill: StoredSkill
    state_skill: StoredSkill | None = None
    enabled: bool = True


def enemy_battle_rules(enemy, catalog):
    """無効な定義は使用せず、編集後の内容を遭遇時に固定する。."""
    skills = {s.id: s for s in catalog.skills if s.enabled}
    triggers = {t.id: t for t in catalog.triggers if t.enabled}
    result = []
    for row in enemy.gambits:
        root = triggers.get(row.trigger_id)
        trigger = root.variant(row.trigger_variant_id) if root else None
        skill = skills.get(row.skill_id)
        if not row.enabled or not trigger or not skill:
            continue
        result.append(
            EnemyBattleRule(
                **trigger.model_dump(include=set(TriggerCondition.model_fields)),
                target=trigger.target or "enemy",
                skill=skill.model_copy(deep=True),
                state_skill=skills.get(trigger.state_skill_id),
            ),
        )
    return result


def choose_enemy_action(save, enemy) -> BattleAction | None:
    """味方には本人を含む。MP不足・満タン回復・条件不一致は次へ進む。."""
    marker, actor = save.battle, enemy.id
    for rule in enemy.gambits:
        if not rule.enabled or not rule.skill.enabled:
            continue
        if marker.schedule.enemy_mp.get(actor, enemy.mp) < rule.skill.mp_cost:
            continue
        candidates = (
            ["player"]
            if rule.target == "enemy"
            else [actor]
            if rule.target == "self"
            else marker.enemies
        )
        skills = {rule.state_skill.id: rule.state_skill} if rule.state_skill else {}
        skill = retarget_skill(rule.skill, "self" if rule.target == "self" else "enemy")
        targets = [
            target
            for target in candidates
            if condition_matches(rule, save, skills, target, actor)
            and can_receive(save, skill, target)
            and (
                save.run.hp
                if target == "player"
                else marker.schedule.enemy_hp.get(target, 0)
            )
            > 0
        ]
        if targets:
            return BattleAction(skill=skill, name=skill.name, target_ids=targets)
    return None
