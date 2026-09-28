"""系エラー."""

from typing import NoReturn

from tanbun.feature.parsing.primitive.dupchk import DuplicationChecker


class InterpreterError(Exception):
    """構文木解析用."""


class SysNetNotFoundError(InterpreterError):
    """ネットワークに含まれないノード."""


class QuotermNotFoundError(InterpreterError):
    """引用用語が存在しない."""


class DefSentenceConflictError(InterpreterError):
    """定義の文が既出."""


class TermResolveError(InterpreterError):
    """用語解決でのエラー."""


class SentenceConflictError(InterpreterError):
    """1文が重複追加."""

    def __init__(self, message: str, *, sentence: str = "") -> None:
        """表示文と、診断位置の特定に使う単文を保持する."""
        super().__init__(message)
        self.sentence = sentence


def sentence_dup_checker() -> DuplicationChecker:
    """For dup checker."""

    def _err(s: str) -> NoReturn:
        msg = f"'{s}'は重複しています"
        raise SentenceConflictError(msg, sentence=str(s))

    return DuplicationChecker(err_fn=_err)
