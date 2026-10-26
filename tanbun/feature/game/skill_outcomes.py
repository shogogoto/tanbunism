"""クイズ一問に対する効果。利用経路・画面表示から独立した共通規則。."""

from hashlib import blake2b

from .catalog import (
    AttackSkill,
    GameCatalog,
    SkillDefinition,
    default_normal_attack,
    parse_skill,
)


def skill_effects(skill: SkillDefinition) -> list[SkillDefinition]:
    """固定済みの効果を設定順に展開する。消費は親スキルだけが持つ."""
    if not skill.effects:
        return [
            skill.with_activation(
                skill.effect_chances.get(skill.template_id, 100),
                skill.id,
            ),
        ]
    identity = {
        key: getattr(skill, key)
        for key in ("id", "name", "icon", "enabled", "animation", "sound", "element")
    }
    components = [
        parse_skill({
            **identity,
            **effect.model_dump(),
            "mp_cost": 0,
            "id": skill.template_ids[index]
            if len(skill.template_ids) == len(skill.effects)
            else "effect_"
            + blake2b(f"{skill.id}:{index}".encode(), digest_size=12).hexdigest(),
        })
        for index, effect in enumerate(skill.effects)
    ]
    return [
        component.with_activation(
            skill.effect_chances.get(component.id, 100),
            f"{skill.id}:{component.id}",
        )
        for component in components
    ]


def has_counter(skill: SkillDefinition) -> bool:
    """反撃型の効果が一つでもあれば、一度の返り討ちを予約する."""
    return any(
        getattr(effect, "on_wrong", None) and effect.on_wrong.type == "counter"
        for effect in skill_effects(skill)
    )


def normal_attack(catalog: GameCatalog | None = None) -> AttackSkill:
    """全キャラクターの最下位ガンビット。装備枠・MPを消費しない。."""
    if catalog:
        skill = next((s for s in catalog.skills if s.id == "normal_attack"), None)
        if isinstance(skill, AttackSkill):
            return skill.model_copy(deep=True)
    return default_normal_attack()


def answer_effect(skill: SkillDefinition, correct: bool) -> SkillDefinition | None:  # noqa: FBT001
    """軽減では同じ効果の量だけを差し替え、対象・持続・演出を維持する。."""
    if correct:
        return skill
    wrong = getattr(skill, "on_wrong", None)
    if wrong is None or wrong.type != "reduced":
        return None
    if skill.effect == "ailment":
        return (
            skill.model_copy(
                update={"duration_turns": wrong.duration_turns or skill.duration_turns},
            )
            if wrong.apply and wrong.duration_turns != 0
            else None
        )
    patch = {"amount": wrong.amount}
    if getattr(wrong, "duration_turns", None) is not None:
        if wrong.duration_turns == 0:
            return None
        patch["duration_turns"] = wrong.duration_turns
    if wrong.amount_mode is not None:
        patch["amount_mode"] = wrong.amount_mode
    if skill.effect == "attack":
        patch["multiplier"] = wrong.multiplier
    return skill.model_copy(update=patch)


def attack_stat(skill) -> str:
    """攻撃カテゴリから参照能力を一意に決める。."""
    return "specialAttack" if skill.damage_type == "special" else "attack"


def defense_stat(skill) -> str:
    """物理は守備力、特殊は特守を参照する。."""
    return "specialDefense" if skill.damage_type == "special" else "defense"


def enemy_attack_value(enemy, skill) -> int:
    """旧敵の特攻は通常攻撃力から補い、素材の倍率を二重適用しない。."""
    if skill.damage_type == "special" and enemy.special_attack is not None:
        return enemy.special_attack
    return (
        enemy.unmodified_attack if enemy.unmodified_attack is not None else enemy.attack
    )


def skill_targets(
    skill,
    enemies: list[str],
    target_ids: list[str] | None = None,
) -> list[str]:
    """単体から五体までを生存順に選び、全体指定は人数制限しない。."""
    if target_ids is not None:
        enemies = [identity for identity in target_ids if identity in enemies]
    return (
        list(enemies)
        if skill.target == "all_enemies" or getattr(skill, "target_count", 1) == "all"
        else enemies[: getattr(skill, "target_count", 1)]
    )


def effect_targets(skill, marker, actor="player", target_ids=None):
    """明示された発動先を優先し、未指定の旧行動は陣営から補う。."""
    if target_ids is None:
        return (
            [actor]
            if skill.target == "self"
            else skill_targets(skill, marker.enemies)
            if actor == "player"
            else ["player"]
        )
    candidates = [
        target
        for target in target_ids
        if target == "player" or target in marker.enemies
    ]
    return skill_targets(skill, candidates)
