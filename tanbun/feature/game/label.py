"""Graph labels for persistent dungeon and region quiz populations."""

from neomodel import AsyncStructuredNode, IntegerProperty, StringProperty


class LDungeon(AsyncStructuredNode):
    """A user's exploration of one resource."""

    __label__ = "Dungeon"
    uid = StringProperty(unique_index=True, required=True)
    user_uid = StringProperty(required=True)
    resource_uid = StringProperty(required=True)


class LDungeonRegion(AsyncStructuredNode):
    """An achievement band with an ordered, cumulative quiz population."""

    __label__ = "DungeonRegion"
    uid = StringProperty(unique_index=True, required=True)
    level = IntegerProperty(required=True)
