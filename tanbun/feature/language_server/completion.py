"""互換用import。実装は共通language serviceに置く."""

from tanbun.feature.language_service.completion import CompletionResult, complete_terms
from tanbun.feature.language_service.symbols import TermSymbol, extract_term_symbols

__all__ = [
    "CompletionResult",
    "TermSymbol",
    "complete_terms",
    "extract_term_symbols",
]
