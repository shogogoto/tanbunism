"""販売設定されたトリガーは購入後に利用する。敵の利用権限とは独立する。."""

from .catalog import GambitShopListing


def trigger_available(catalog, economy, trigger_id) -> bool:
    """販売未設定の既存トリガーを維持し、購入済みなら再購入を要さない。."""
    return trigger_id in economy.unlocked_triggers or not any(
        isinstance(row, GambitShopListing) and row.trigger_id == trigger_id
        for row in catalog.shop
    )


def rule_available(catalog, economy, rule) -> bool:
    """購入制御を、条件を直接送って迂回できないようにする。."""
    if rule.trigger_id:
        return trigger_available(catalog, economy, rule.trigger_id)
    return not any(isinstance(row, GambitShopListing) for row in catalog.shop)
