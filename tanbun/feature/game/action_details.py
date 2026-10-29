"""結果表示のための条件名と対象を、行動時の情報から固定する."""

from pydantic import BaseModel, Field

from .skill_outcomes import effect_targets, skill_effects


class BattleActionDetails(BaseModel):
    """クイズの正誤・戦闘結果本文とは別に表示する自動行動の根拠."""

    trigger: str | None = Field(default=None, max_length=1000)
    targets: list[str] = Field(default_factory=list, max_length=21)


def describe_action(action, skill, marker, names: dict[str, str], actor="player"):
    """各効果の実際の対象数で絞り、不発でも選択された対象は表示する."""
    identities = dict.fromkeys(
        target
        for effect in skill_effects(skill)
        for target in effect_targets(
            effect,
            marker,
            actor,
            action.target_ids if action else None,
        )
    )
    return BattleActionDetails(
        trigger=action.trigger_name if action else "いつでも（通常攻撃）",  # noqa: RUF001
        targets=[names.get(identity, "敵") for identity in identities],
    )
