"""関係関連."""

from collections.abc import Sequence
from enum import StrEnum
from functools import cache
from itertools import islice
from typing import Any, Final, Self

from tanbun.feature.domain.graph.edge_type import EdgeType

QUIZ_REL_EDGE_TYPES: Final = (
    EdgeType.SIBLING,
    EdgeType.BELOW,
    EdgeType.RESOLVED,
    EdgeType.TO,
    EdgeType.EXAMPLE,
    EdgeType.ANTI,
)


class QuizRel(StrEnum):
    """クイズ対象との関係."""

    # Part
    PARENT = "親"
    DETAIL = "詳細"  # belowとその兄弟
    PEER = "同階層"

    # logic
    PREMISE = "前提"
    CONCLUSION = "結論"

    # refer 分かりにくい表現
    REFER = "用語参照"  # targetが参照する、根, source側
    REFERRED = "被参照"  # targetが参照される、葉, destination側

    # hierarchy
    GENERAL = "一般"
    EXAMPLE = "具体例"
    ANTI = "反対"

    @property
    def edge(self) -> tuple[EdgeType, bool]:
        """Return the knowledge edge and its direction from the quiz target."""
        return {
            self.PARENT: (EdgeType.BELOW, False),
            self.DETAIL: (EdgeType.BELOW, True),
            self.PEER: (EdgeType.SIBLING, True),
            self.PREMISE: (EdgeType.TO, False),
            self.CONCLUSION: (EdgeType.TO, True),
            self.REFER: (EdgeType.RESOLVED, False),
            self.REFERRED: (EdgeType.RESOLVED, True),
            self.GENERAL: (EdgeType.EXAMPLE, False),
            self.EXAMPLE: (EdgeType.EXAMPLE, True),
            self.ANTI: (EdgeType.ANTI, True),
        }[self]

    @classmethod
    @cache
    def forwards(cls) -> dict:
        """正順辞書."""
        return {
            EdgeType.TO: cls.CONCLUSION,
            EdgeType.RESOLVED: cls.REFERRED,
            EdgeType.EXAMPLE: cls.EXAMPLE,
            EdgeType.ANTI: cls.ANTI,
            cls.DETAIL: cls.DETAIL,
            cls.PEER: cls.PEER,
        }

    @classmethod
    @cache
    def backwards(cls) -> dict:
        """正順辞書."""
        return {
            EdgeType.TO: cls.PREMISE,
            EdgeType.RESOLVED: cls.REFER,
            EdgeType.EXAMPLE: cls.GENERAL,
            EdgeType.ANTI: cls.ANTI,
            cls.DETAIL: cls.PARENT,
            cls.PEER: cls.PEER,
        }

    @classmethod
    def of(
        cls,
        edge_types: Sequence[EdgeType],
        is_forward: bool,  # noqa: FBT001
    ) -> list[Self]:
        """クイズ関係タイプへ変換."""
        ets = edgetype2rel(edge_types)
        if is_forward:
            retval = [cls.forwards()[et] for et in ets]
        else:
            retval = reversed([cls.backwards()[et] for et in ets])
        return list(retval)


def count_consecutive_elm(seq: Sequence, i_start: int, val: Any):
    """指定番号以降の特定要素の連続回数を数える."""
    if i_start >= len(seq):
        return 0
    count = 0
    # start_index以降の要素を1つずつ取り出す
    for item in islice(seq, i_start, None):
        if item == val:
            count += 1
        else:
            break
    return count


def edgetype2rel(ets: Sequence[EdgeType | QuizRel]) -> Sequence[EdgeType | QuizRel]:
    """再帰的に詳細関係への変換."""
    retval = list(ets)
    if EdgeType.BELOW not in ets:
        if EdgeType.SIBLING in ets:
            i_below = ets.index(EdgeType.SIBLING)
            n = count_consecutive_elm(ets, i_below, EdgeType.SIBLING)
            retval[i_below : i_below + n + 1] = [QuizRel.PEER]
            return edgetype2rel(retval)

        return ets
    i_below = ets.index(EdgeType.BELOW)

    try:
        n = count_consecutive_elm(retval, i_below + 1, EdgeType.SIBLING)
        retval[i_below : i_below + n + 1] = [QuizRel.DETAIL]
    except IndexError:  # [BELOW]
        return [QuizRel.DETAIL]
    return edgetype2rel(retval)
