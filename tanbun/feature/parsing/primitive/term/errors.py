"""term errors."""


class TermError(Exception):
    """用語系エラー."""


class TermMergeError(TermError):
    """用語の合併を許さないのに."""


class TermConflictError(TermError):
    """用語の衝突."""

    def __init__(self, message: str, *, names: tuple[str, ...] = ()) -> None:
        """表示文と、診断位置の特定に使う用語名を保持する."""
        super().__init__(message)
        self.names = names


class AliasContainsMarkError(TermError):
    """別名にマークを含んだらダメ."""

    def __init__(self, message: str, *, alias: str = "") -> None:
        """表示文と、診断位置の特定に使うaliasを保持する."""
        super().__init__(message)
        self.alias = alias


class TermResolveError(TermError):
    """用語解決に失敗."""


class MarkUncontainedError(TermError):
    """マークが用語解決器に含まれない."""
