"""ガンビットはバフ・デバフ適用後の能力を比較し、候補の順位を決める."""

from fractions import Fraction
from operator import eq, ge, lt

from .effects import modified_stat

STAT_KEYS = {"special_attack": "specialAttack", "special_defense": "specialDefense"}
COMPARISONS = {"above": ge, "below": lt, "equal": eq}


def stat_matches(rule, save, target):
    """順位指定は値が存在すれば候補とし、数値指定は境界を比較する."""
    value = stat_value(rule, save, target)
    return value is not None and (
        rule.comparison in {"lowest", "highest"}
        or COMPARISONS[rule.comparison](value, rule.threshold)
    )


def resource_values(save, target, resource):
    """HP/MPの現在値と、バブルを含む最大値を返す."""
    if target == "player":
        return getattr(save.run, resource), getattr(
            save.run,
            "maxHp" if resource == "hp" else "maxMp",
        )
    schedule = save.battle.schedule
    if not schedule:
        return None, None
    return (
        getattr(schedule, f"enemy_{resource}").get(target),
        getattr(schedule, f"enemy_max_{resource}").get(target),
    )


def stat_value(rule, save, target):
    """割合は分数のまま比較し、丸めによる同率や境界ずれを避ける."""
    if rule.stat in {"hp", "mp"}:
        current, maximum = resource_values(save, target, rule.stat)
        if current is None or maximum is None:
            return None
        if rule.stat_basis == "percent":
            return Fraction(current * 100, maximum) if maximum > 0 else None
        return maximum if rule.stat_basis == "maximum" else current
    marker = save.battle
    schedule = marker.schedule
    if rule.stat == "speed" and schedule:
        return schedule.speeds.get(target)
    key = STAT_KEYS.get(rule.stat, rule.stat)
    base = schedule.stat_bases.get(target, {}).get(key) if schedule else None
    if base is None and target == "player":
        base = getattr(save.run, key)
    return modified_stat(base, marker, target, key) if base is not None else None


def order_targets(rule, save, targets):
    """順位は候補全体の順序。対象数はスキルが決め、同率順は固定する."""
    if rule.condition != "stat" or rule.comparison not in {"lowest", "highest"}:
        return targets
    values = {target: stat_value(rule, save, target) for target in targets}
    return sorted(
        (target for target in targets if values[target] is not None),
        key=lambda target: values[target],
        reverse=rule.comparison == "highest",
    )
