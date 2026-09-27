"""mark errors."""


class MarkContainsMarkError(Exception):
    """マーク内にマークを含む."""

    def __init__(self, message: str, *, source: str | None = None) -> None:
        """表示文と、診断位置の特定に使う入力を保持する."""
        super().__init__(message)
        self.source = source


class EmptyMarkError(Exception):
    """mark内が空."""


class PlaceHolderMappingError(Exception):
    """プレースホルダーを置換するための対応づけに失敗."""
