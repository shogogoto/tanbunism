"""取得済みのPower・関係数・設定から敵能力を導く。DBは照会しない."""

from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING
from uuid import UUID

from .balance_models import GameBalance
from .catalog import GameCatalog
from .combat_models import EnemyStats
from .effects import effect_amount
from .enemy_gambits import enemy_battle_rules
from .skill_outcomes import skill_effects

if TYPE_CHECKING:
    from .state import BattleMarker


def label_enemies(
    enemies: list[EnemyStats],
    marker: BattleMarker | None,
) -> list[EnemyStats]:
    """遭遇時に付けた固定記号を、表示名とメッセージで共有する."""
    labels = marker.enemyLabels if marker else {}
    return [
        enemy.model_copy(
            update={
                "name": f"{enemy.base_name or enemy.name}{labels[enemy.id]}",
                "base_name": enemy.base_name or enemy.name,
            },
        )
        if enemy.id in labels
        else enemy
        for enemy in enemies
    ]


def style_enemies(
    enemies: list[EnemyStats],
    catalog: GameCatalog,
    *,
    preserve_stats: bool = False,
) -> list[EnemyStats]:
    """クイズの固定セットを変えず、同じ種類には同じ素材を割り当てる。."""
    from .skill_outcomes import normal_attack  # noqa: PLC0415

    skills = {s.id: s for s in catalog.skills if s.enabled}
    result = []
    for source_enemy in enemies:
        enemy = source_enemy
        boss = enemy.id.startswith("boss:")
        templates = [e for e in catalog.enemies if e.enabled and e.boss == boss]
        if not templates:
            fallback = normal_attack(catalog)
            result.append(
                enemy.model_copy(
                    update={
                        "attack_skill": fallback,
                        "animation": fallback.animation,
                        "sound": fallback.sound,
                    },
                ),
            )
            continue
        identity = enemy.id.split("#", 1)[0]
        seed = sum((i + 1) * ord(c) for i, c in enumerate(identity))
        template = templates[seed % len(templates)]
        if not preserve_stats:
            values = {
                key: getattr(enemy, key)
                for key in type(template.multipliers).model_fields
            }
            values["special_attack"] = (
                enemy.special_attack
                if enemy.special_attack is not None
                else enemy.attack
            )
            values["speed"] = (
                template.speed if template.speed is not None else enemy.speed
            )
            enemy = enemy.model_copy(
                update={
                    key: min(
                        100000,
                        max(
                            1 if key in {"hp", "speed"} else 0,
                            ceil(value * getattr(template.multipliers, key)),
                        ),
                    )
                    for key, value in values.items()
                },
            )
        skill = skills.get(template.attack_skill_id) or normal_attack(catalog)
        attack_effect = next(
            (effect for effect in skill_effects(skill) if effect.effect == "attack"),
            None,
        )
        attack = (
            min(
                100000,
                int(enemy.attack * attack_effect.multiplier)
                + effect_amount(attack_effect, enemy.attack),
            )
            if attack_effect and not preserve_stats
            else enemy.attack
        )
        result.append(
            enemy.model_copy(
                update={
                    "on_hit_skill": skills.get(template.on_hit_skill_id),
                    "resistances": [
                        key
                        for key in template.resistances
                        if any(e.id == key and e.enabled for e in catalog.elements)
                    ],
                    "weaknesses": [
                        key
                        for key in template.weaknesses
                        if any(e.id == key and e.enabled for e in catalog.elements)
                    ],
                    "attack_skill": skill,
                    "gambits": enemy_battle_rules(template, catalog),
                    "icon": template.icon,
                    "name": f"達成度 {enemy.region + 1}の{template.name}"
                    if boss
                    else template.name,
                    "attack": attack,
                    "unmodified_attack": enemy.attack,
                    "speed": enemy.speed,
                    "animation": skill.animation if skill else "none",
                    "sound": skill.sound if skill else "auto",
                },
            ),
        )
    return result


def derive_enemies(
    content: dict,
    relations: dict,
    power: int,
    balance: GameBalance,
) -> list[EnemyStats]:
    """旧snapshotのHP/攻は無視し、同じIDの能力をその都度導出する."""
    quizzes = content.get("quizzes", [])
    selected = (content.get("battle") or {}).get("quizIndices", {})
    enemies = []
    for key, pool in content.get("regionEnemies", {}).items():
        region = int(key)
        for item in pool:
            indices = item.get("quizIndexes") or [item.get("quizIndex", -1)]
            indices = [
                index
                for index in indices
                if isinstance(index, int) and 0 <= index < len(quizzes)
            ]
            if not indices:
                continue
            valid = [
                index
                for index in indices
                if UUID(str(quizzes[index]["quiz_id"])).hex in relations
            ]
            if not valid:
                continue
            relation_count = round(
                sum(
                    relations[UUID(str(quizzes[index]["quiz_id"])).hex]
                    for index in valid
                )
                / len(valid),
            )
            selected_index = selected.get(item["id"])
            hp, attack = balance.enemy_stats(
                power,
                relation_count,
                region,
                variation_key=item["id"],
            )
            enemies.append(
                EnemyStats(
                    id=item["id"],
                    name=item["name"],
                    quizIndex=(selected_index if selected_index in valid else valid[0]),
                    quizIndexes=valid,
                    hp=hp,
                    attack=attack,
                    speed=balance.enemy_speed_stat(power, region, item["id"]),
                    **balance.enemy_extra_stats(power, region, item["id"]),
                    relations=relation_count,
                    region=region,
                ),
            )
    boss_enemy = derive_boss(content, relations, power, balance)
    if boss_enemy:
        enemies.append(boss_enemy)
    by_id = {enemy.id: enemy for enemy in enemies}
    for instance in (content.get("battle") or {}).get("enemies", []):
        if instance in by_id:
            continue
        base = by_id.get(instance.split("#", 1)[0])
        if base:
            enemies.append(
                base.model_copy(
                    update={
                        "id": instance,
                        "quizIndex": selected.get(instance, base.quizIndex),
                    },
                ),
            )
    return enemies


def derive_boss(
    content: dict,
    relations: dict,
    power: int,
    balance: GameBalance,
) -> EnemyStats | None:
    """通常の敵と同じ基礎能力を作り、個性の倍率は敵定義側だけで適用する。."""
    marker = content.get("battle") or {}
    level = marker.get("bossLevel")
    if not level:
        return None
    identity = marker["enemies"][0]
    pool = set(marker.get("bossQuizIds", []))
    indexes = [
        i
        for i, q in enumerate(content.get("quizzes", []))
        if UUID(str(q["quiz_id"])).hex in pool
        and UUID(str(q["quiz_id"])).hex in relations
    ]
    if not indexes:
        return None
    hp, attack = balance.enemy_stats(power, 0, level - 1, variation_key=identity)
    extra = balance.enemy_extra_stats(power, level - 1, identity)
    return EnemyStats(
        id=identity,
        name=f"達成度 {level}のボス",
        quizIndexes=indexes,
        quizIndex=marker.get("quizIndices", {}).get(identity, indexes[0]),
        hp=hp,
        attack=attack,
        speed=balance.enemy_speed_stat(power, level - 1, identity),
        **extra,
        relations=0,
        region=level - 1,
    )
