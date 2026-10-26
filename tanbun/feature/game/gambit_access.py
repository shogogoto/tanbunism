"""販売設定されたトリガーは購入後に利用する。敵の利用権限とは独立する。."""

from .catalog import GambitShopListing, trigger_unlock_id


def trigger_available(catalog, economy, trigger_id, variant_id=None) -> bool:
    """販売未設定の既存トリガーを維持し、購入済みなら再購入を要さない。."""
    key = trigger_unlock_id(trigger_id, variant_id)
    if key in economy.unlocked_triggers:
        return True
    if any(
        isinstance(row, GambitShopListing)
        and row.trigger_id == trigger_id
        and row.trigger_variant_id == variant_id
        for row in catalog.shop
    ):
        return False
    return not variant_id or trigger_available(catalog, economy, trigger_id)


def rule_available(catalog, economy, rule) -> bool:
    """購入制御を、条件を直接送って迂回できないようにする。."""
    if rule.trigger_id:
        return trigger_available(
            catalog,
            economy,
            rule.trigger_id,
            rule.trigger_variant_id,
        )
    return not any(isinstance(row, GambitShopListing) for row in catalog.shop)
