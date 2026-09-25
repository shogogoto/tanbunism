"""neomodel label."""

from abc import abstractmethod
from typing import override

from neomodel import (
    AliasProperty,
    ArrayProperty,
    AsyncRelationshipFrom,
    AsyncRelationshipManager,
    AsyncRelationshipTo,
    AsyncStructuredNode,
    AsyncZeroOrOne,
    DateProperty,
    DateTimeNeo4jFormatProperty,
    FloatProperty,
    FulltextIndex,
    IntegerProperty,
    StringProperty,
    UniqueIdProperty,
)

from tanbun.feature.user.label import LUser  # noqa: F401

from .mapper import Entry, MFolder, MResource


class LHead(AsyncStructuredNode):
    """見出し."""

    __label__ = "Head"
    uid = UniqueIdProperty()
    val = StringProperty(index=True, required=True)  # , max_length=MAX_CHARS)


class LEntry(AsyncStructuredNode):
    """情報源とその容れ物の同一視."""

    __abstract_node__ = True
    __label__ = "Entry"
    uid = UniqueIdProperty()  # parents辿る手間を避けるため

    owner: AsyncRelationshipManager = AsyncRelationshipTo(
        "LUser",
        "OWNED",
        cardinality=AsyncZeroOrOne,
    )

    parent: AsyncRelationshipManager = AsyncRelationshipTo(
        "LFolder",
        "PARENT",
        cardinality=AsyncZeroOrOne,
    )

    @property
    @abstractmethod
    def frozen(self) -> Entry:
        """namespace用hashable."""
        raise NotImplementedError


class LResource(LEntry):
    """情報源 rootの見出し(H1)."""

    __label__ = "Resource"
    title = StringProperty(
        index=True,
        required=True,
        fulltext_index=FulltextIndex(),
    )  # userの中でユニーク
    name = AliasProperty("title")
    resource_key = StringProperty(unique_index=True)
    authors = ArrayProperty(StringProperty())
    published = DateProperty()
    urls = ArrayProperty(StringProperty())

    # file由来
    txt_hash = IntegerProperty()
    updated = DateTimeNeo4jFormatProperty()

    @property
    @override
    def frozen(self) -> MResource:
        return MResource(
            name=self.name,
            element_id_property=self.element_id,
            uid=self.uid,
            authors=self.authors,
            published=self.published,
            urls=self.urls,
            txt_hash=self.txt_hash,
            updated=self.updated,
        )

    cached_stats: AsyncRelationshipManager = AsyncRelationshipTo(
        "LResourceStatsCache",
        "STATS",
        cardinality=AsyncZeroOrOne,
    )


class LResourceStatsCache(AsyncStructuredNode):
    """リソースの統計情報キャッシュ."""

    __label__ = "ResourceStatsCache"
    n_char = IntegerProperty()
    n_sentence = IntegerProperty()
    n_term = IntegerProperty()
    n_edge = IntegerProperty()
    n_isolation = IntegerProperty()
    n_axiom = IntegerProperty()
    n_unrefered = IntegerProperty()

    # computed
    average_degree = FloatProperty()
    density = FloatProperty()
    diameter = FloatProperty()
    radius = FloatProperty()
    n_scc = IntegerProperty()


class LFolder(LEntry):
    """sysnetの入れ物."""

    __label__ = "Folder"
    name = StringProperty(index=True)

    children: AsyncRelationshipManager = AsyncRelationshipFrom(
        "LEntry",
        "PARENT",
        cardinality=AsyncZeroOrOne,
    )

    @property
    @override
    def frozen(self) -> MFolder:
        """hashableへ."""
        return MFolder(
            name=self.name,
            element_id_property=self.element_id,
            uid=self.uid,
        )
