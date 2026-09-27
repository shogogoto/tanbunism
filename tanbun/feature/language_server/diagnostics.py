"""パーサーの例外をエディタ向け診断に変換する."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from tanbun.feature.parsing.check.domain import inspect_document
from tanbun.feature.parsing.issue import ParseIssue
from tanbun.feature.parsing.primitive.term import Term


@dataclass(frozen=True)
class ParseStatistics:
    """文書解析の所要時間と規模."""

    duration_ms: float
    line_count: int
    term_count: int | None
    node_count: int | None
    relation_count: int | None


@dataclass(frozen=True)
class DocumentAnalysis:
    """文書の診断と統計値."""

    diagnostics: tuple[ParseIssue, ...]
    statistics: ParseStatistics


def diagnose(text: str) -> list[ParseIssue]:
    """文書全体を検査する."""
    return list(analyze(text).diagnostics)


def analyze(text: str) -> DocumentAnalysis:
    """文書全体を解析し、診断と統計値を返す."""
    started = perf_counter()
    inspection = inspect_document(text)
    if inspection.issue is not None:
        return DocumentAnalysis(
            diagnostics=(*inspection.warnings, inspection.issue),
            statistics=ParseStatistics(
                duration_ms=_elapsed_ms(started),
                line_count=len(text.splitlines()),
                term_count=None,
                node_count=None,
                relation_count=None,
            ),
        )
    assert inspection.network is not None
    network = inspection.network
    return DocumentAnalysis(
        diagnostics=inspection.warnings,
        statistics=ParseStatistics(
            duration_ms=_elapsed_ms(started),
            line_count=len(text.splitlines()),
            term_count=sum(isinstance(node, Term) for node in network.g.nodes),
            node_count=network.g.number_of_nodes(),
            relation_count=network.g.number_of_edges(),
        ),
    )


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000
