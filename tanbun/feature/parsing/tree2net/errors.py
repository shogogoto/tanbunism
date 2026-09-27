"""構文木から知識グラフへ変換するときのエラー."""

from __future__ import annotations

from typing import Any


class OrphanRelationError(Exception):
    """接続元の単文がない関係行."""

    def __init__(self, target: Any) -> None:  # noqa: D107
        self.target = str(target)
        super().__init__(self.target)
