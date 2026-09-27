"""エディタやHTTPから共有するTanbun言語機能."""

from .analysis import DocumentAnalysis, ParseStatistics, analyze, diagnose
from .completion import CompletionResult, complete_terms
from .definition import (
    DefinitionLocation,
    SourceDocument,
    find_definition,
    find_definitions,
)
from .references import (
    ReferenceGroup,
    ReferenceKind,
    ReferenceLocation,
    find_references,
    group_references,
)
from .symbols import TermSymbol, extract_term_symbols

__all__ = [
    "CompletionResult",
    "DefinitionLocation",
    "DocumentAnalysis",
    "ParseStatistics",
    "ReferenceGroup",
    "ReferenceKind",
    "ReferenceLocation",
    "SourceDocument",
    "TermSymbol",
    "analyze",
    "complete_terms",
    "diagnose",
    "extract_term_symbols",
    "find_definition",
    "find_definitions",
    "find_references",
    "group_references",
]
