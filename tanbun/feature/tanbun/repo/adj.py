"""隣接クエリ関連."""

from enum import StrEnum

from tanbun.feature.domain.graph.edge_type import Direction, EdgeType


def detail_match(sent_var: str, dist: int | None) -> str:
    """隣接する文のIDを返す."""
    if dist == 1:
        return f"""
        // 1個下の兄弟まで
        OPTIONAL MATCH ({sent_var})-[:BELOW]->(detail1:Sentence)
        OPTIONAL MATCH (detail1)-[:SIBLING]->*(detail2:Sentence)
        UNWIND [detail1, detail2] AS detail
    """
    d = "" if dist is None else str(dist)
    return f"""
        OPTIONAL MATCH ({sent_var})-[:BELOW]->(:Sentence)
            -[:SIBLING|BELOW]->{{0, {d}}}(detail:Sentence)
    """


def q_arrow(name: str, d: Direction) -> str:
    """矢印."""
    match d:
        case Direction.FORWARD:
            return f"-[:{name}]->"
        case Direction.BACKWARD:
            return f"<-[:{name}]-"
        case Direction.BOTH:
            return f"-[:{name}]-"


class AdjType(StrEnum):
    """隣接タイプ (HTTP/FastAPI互換)."""

    PREMISE = "premise"
    CONCLUSION = "conclusion"
    REFERRED = "referred"
    REFER = "refer"
    ABSTRACT = "abstract"
    EXAMPLE = "example"
    DETAIL = "detail"
    SIBLING = "sibling"
    PARENT = "parent"
    ANTI = "anti"

    @classmethod
    def location_types(cls) -> list["AdjType"]:
        """location付きでのクエリで使う想定."""
        return [
            cls.PREMISE,
            cls.CONCLUSION,
            cls.REFERRED,
            cls.REFER,
            cls.ABSTRACT,
            cls.EXAMPLE,
            cls.DETAIL,
            cls.ANTI,
        ]

    @property
    def edge_type(self) -> tuple[EdgeType, Direction]:
        """EdgeType."""
        return {
            AdjType.PREMISE: (EdgeType.TO, Direction.BACKWARD),
            AdjType.CONCLUSION: (EdgeType.TO, Direction.FORWARD),
            AdjType.REFERRED: (EdgeType.RESOLVED, Direction.BACKWARD),
            AdjType.REFER: (EdgeType.RESOLVED, Direction.FORWARD),
            AdjType.ABSTRACT: (EdgeType.EXAMPLE, Direction.BACKWARD),
            AdjType.EXAMPLE: (EdgeType.EXAMPLE, Direction.FORWARD),
            AdjType.DETAIL: (EdgeType.BELOW, Direction.FORWARD),
            AdjType.SIBLING: (EdgeType.SIBLING, Direction.BOTH),
            AdjType.PARENT: (EdgeType.BELOW, Direction.BACKWARD),
            AdjType.ANTI: (EdgeType.ANTI, Direction.BOTH),
        }[self]

    def match(self, sent_var: str, dist: int | None) -> str:
        """Match query."""
        if self == AdjType.DETAIL:
            return detail_match(sent_var, dist)
        if self == AdjType.ANTI:
            return f"""
            OPTIONAL MATCH ({sent_var})-[:ANTI]-(anti:Sentence)
            WHERE anti <> {sent_var}
            """
        name = self.value
        et, direc = self.edge_type
        ar = q_arrow(et.name, direc)
        d = "" if dist is None else str(dist)
        return f"""
        OPTIONAL MATCH ({sent_var}){ar}{{1,{d}}}({name}:Sentence)"""

    @property
    def collect(self) -> str:
        """uuid集計."""
        name = self.value
        return f"""
            , COLLECT(DISTINCT {name}.uid) AS {name}s"""
