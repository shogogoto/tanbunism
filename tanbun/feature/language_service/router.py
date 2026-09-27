"""Web editor向けのTanbun言語サービスAPI."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from tanbun.feature.parsing.issue import ParseIssue, SourceRange

from .analysis import ParseStatistics, analyze
from .completion import CompletionResult, complete_terms
from .definition import SourceDocument, find_definition
from .position import DocumentPosition, offset_to_position, position_to_offset
from .references import find_references, group_references

MAX_DOCUMENT_LENGTH = 1_000_000


class PositionBody(BaseModel, frozen=True):
    """HTTP上の0始まり文書位置."""

    line: int = Field(ge=0)
    character: int = Field(ge=0)


class DocumentBody(BaseModel, frozen=True):
    """解析対象文書."""

    text: str = Field(max_length=MAX_DOCUMENT_LENGTH)


class PositionedDocumentBody(DocumentBody, frozen=True):
    """カーソル位置を含む解析対象文書."""

    position: PositionBody


class SourceRangeResult(BaseModel, frozen=True):
    """APIで返すソース範囲."""

    line: int
    start_character: int
    end_character: int


class DiagnosticResult(BaseModel, frozen=True):
    """APIで返す利用者向け診断."""

    code: str
    message: str
    suggestion: str | None
    severity: str
    source_range: SourceRangeResult


class StatisticsResult(BaseModel, frozen=True):
    """APIで返す文書統計."""

    duration_ms: float
    line_count: int
    term_count: int | None
    node_count: int | None
    relation_count: int | None


class AnalysisResult(BaseModel, frozen=True):
    """診断と文書統計."""

    diagnostics: list[DiagnosticResult]
    statistics: StatisticsResult


class CompletionSymbolResult(BaseModel, frozen=True):
    """補完候補."""

    label: str
    detail: str


class CompletionResultBody(BaseModel, frozen=True):
    """補完候補と置換範囲."""

    replace_range: SourceRangeResult
    symbols: list[CompletionSymbolResult]
    closing: str


class ReferenceResult(BaseModel, frozen=True):
    """参照位置."""

    source_range: SourceRangeResult


class ReferenceGroupResult(BaseModel, frozen=True):
    """種類ごとにまとめた参照位置."""

    kind: str
    references: list[ReferenceResult]


router = APIRouter(prefix="/language", tags=["language"])


@router.post("/analysis")
def analyze_document(body: DocumentBody) -> AnalysisResult:
    """DBに依存せず文書の診断と統計を返す."""
    result = analyze(body.text)
    return AnalysisResult(
        diagnostics=[_diagnostic_result(item) for item in result.diagnostics],
        statistics=_statistics_result(result.statistics),
    )


@router.post("/completion")
def complete_document(body: PositionedDocumentBody) -> CompletionResultBody | None:
    """カーソル位置で利用できる用語補完を返す."""
    result = complete_terms(body.text, _request_offset(body))
    return None if result is None else _completion_result(body.text, result)


@router.post("/definition")
def define_document(body: PositionedDocumentBody) -> SourceRangeResult | None:
    """カーソル下の用語参照の定義位置を返す."""
    result = find_definition(body.text, _request_offset(body))
    return None if result is None else _source_range_result(result)


@router.post("/references")
def reference_document(body: PositionedDocumentBody) -> list[ReferenceGroupResult]:
    """カーソル下の用語を参照する位置を種類付きで返す."""
    document = SourceDocument(uri="document", text=body.text)
    locations = find_references(document, _request_offset(body))
    return [
        ReferenceGroupResult(
            kind=group.kind.value,
            references=[
                ReferenceResult(
                    source_range=_source_range_result(location.source_range),
                )
                for location in group.locations
            ],
        )
        for group in group_references(locations)
    ]


def language_router() -> APIRouter:
    """Tanbun言語サービスのrouterを返す."""
    return router


def _request_offset(body: PositionedDocumentBody) -> int:
    return position_to_offset(
        body.text,
        DocumentPosition(body.position.line, body.position.character),
    )


def _diagnostic_result(issue: ParseIssue) -> DiagnosticResult:
    return DiagnosticResult(
        code=issue.code,
        message=issue.message,
        suggestion=issue.suggestion,
        severity=issue.severity.value,
        source_range=_source_range_result(issue.source_range),
    )


def _statistics_result(statistics: ParseStatistics) -> StatisticsResult:
    return StatisticsResult(
        duration_ms=statistics.duration_ms,
        line_count=statistics.line_count,
        term_count=statistics.term_count,
        node_count=statistics.node_count,
        relation_count=statistics.relation_count,
    )


def _completion_result(text: str, result: CompletionResult) -> CompletionResultBody:
    start = offset_to_position(text, result.start_offset)
    end = offset_to_position(text, result.end_offset)
    return CompletionResultBody(
        replace_range=SourceRangeResult(
            line=start.line,
            start_character=start.character,
            end_character=end.character,
        ),
        symbols=[
            CompletionSymbolResult(label=symbol.label, detail=symbol.detail)
            for symbol in result.symbols
        ],
        closing=result.closing,
    )


def _source_range_result(source_range: SourceRange) -> SourceRangeResult:
    return SourceRangeResult(
        line=source_range.line,
        start_character=source_range.start_character,
        end_character=source_range.end_character,
    )
