"""復習実績の検索用インデックス・一意制約."""

from neomodel import AsyncStructuredNode, IntegerProperty, StringProperty


class LResourceXpEvent(AsyncStructuredNode):
    """削除可能な対象ノードから独立した、日別のXP記録."""

    __label__ = "ResourceXpEvent"
    key = StringProperty(unique_index=True, required=True)
    user_id = StringProperty(index=True, required=True)
    resource_id = StringProperty(index=True, required=True)
    resource_name = StringProperty(required=True)
    source = StringProperty(required=True)
    xp = IntegerProperty(required=True)
    subject = StringProperty(required=True)
    earned_on = StringProperty(required=True)
