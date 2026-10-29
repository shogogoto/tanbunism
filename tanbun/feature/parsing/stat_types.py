"""CLIの選択肢。重いsysnetパッケージから独立させる."""

from enum import StrEnum
from typing import NamedTuple


class Nw1N1Label(StrEnum):
    """文脈タイプ."""

    DETAIL = "detail"
    REFER = "refer"
    REFERRED = "referred"
    PREMISE = "premise"
    CONCLUSION = "conclusion"
    EXAMPLE = "example"
    GENERAL = "general"


class Nw1N1Recursive(NamedTuple):
    """どのlabelで何回再帰的に返すか."""

    label: Nw1N1Label
    n_rec: int
