"""エラー."""


class MultiEdgesError(Exception):
    """複数のエッジが伸びてるわけがない."""


class GraphCycleError(ValueError):
    """有限の入れ子構造へ変換できない循環を検出した."""
