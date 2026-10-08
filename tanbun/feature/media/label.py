"""Cleanup job identity must be unique even during concurrent requests."""

from neomodel import AsyncStructuredNode, StringProperty


class LImageCleanup(AsyncStructuredNode):
    """Unreferenced assets scheduled for deletion."""

    __label__ = "ImageCleanup"
    key = StringProperty(unique_index=True, required=True)


class LImageCleanupQueue(AsyncStructuredNode):
    """Serialize claims before reading job properties across web processes."""

    __label__ = "ImageCleanupQueue"
    key = StringProperty(unique_index=True, required=True)
