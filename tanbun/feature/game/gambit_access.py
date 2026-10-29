"""プレイヤーは入手済みの条件だけを使う。敵の利用権限とは独立する。."""

from .catalog import trigger_unlock_id


def trigger_available(catalog, economy, trigger_id, variant_id=None) -> bool:
    """基本と各変型を別々の所有権として扱い、販売の有無には依存しない。."""
    trigger = next(
        (t for t in catalog.triggers if t.id == trigger_id and t.enabled),
        None,
    )
    return bool(
        trigger
        and trigger.variant(variant_id)
        and trigger_unlock_id(trigger_id, variant_id) in economy.unlocked_triggers,
    )


def rule_available(catalog, economy, rule) -> bool:
    """購入制御を、条件を直接送って迂回できないようにする。."""
    if rule.trigger_id:
        return trigger_available(
            catalog,
            economy,
            rule.trigger_id,
            rule.trigger_variant_id,
        )
    return False
