"""クイズ一問に対する効果。利用経路・画面表示から独立した共通規則。."""

from .catalog import AttackSkill, GameCatalog, SkillDefinition, default_normal_attack


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
        return skill if wrong.apply else None
    patch = {"amount": wrong.amount}
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


def skill_targets(skill, enemies: list[str]) -> list[str]:
    """単体から五体までを生存順に選び、全体指定は人数制限しない。."""
    return (
        list(enemies)
        if skill.target == "all_enemies"
        else enemies[: getattr(skill, "target_count", 1)]
    )
