"""一行動・一効果・一対象の抽選。再送や再読み込みでは結果を変えない。."""

from hashlib import blake2b

FULL_CHANCE = 100


def effect_activates(skill, marker, target: str, actor="player") -> bool:
    """付与時だけ判定し、継続中の効果には使わない。."""
    chance = skill.activation_chance
    if chance in {0, FULL_CHANCE}:
        return chance == FULL_CHANCE
    action_id = (marker.endAction or {}).get("id", "")
    key = f"{marker.id}:{marker.turn}:{action_id}:{actor}:{skill.chance_key}:{target}"
    roll = int.from_bytes(blake2b(key.encode(), digest_size=8).digest()) % 10000
    return roll < chance * 100
