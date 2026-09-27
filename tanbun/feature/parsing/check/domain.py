"""Tanbun文書をDBに依存せず検査する."""

from __future__ import annotations

from dataclasses import dataclass

from tanbun.feature.parsing.issue import (
    ParseIssue,
    exception_to_parse_issue,
    lint_parse_style,
)
from tanbun.feature.parsing.primitive.quoterm.errors import QuotermNotFoundError
from tanbun.feature.parsing.primitive.term.errors import MarkUncontainedError
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.tree2net import parse2net_uncached

from .unresolved import find_unresolved_reference_issues


@dataclass(frozen=True)
class DocumentInspection:
    """文書のパース結果."""

    network: SysNet | None
    errors: tuple[ParseIssue, ...]
    warnings: tuple[ParseIssue, ...]

    @property
    def issue(self) -> ParseIssue | None:
        """後方互換用に最初のエラーを返す."""
        return self.errors[0] if self.errors else None

    @property
    def is_valid(self) -> bool:
        """文書を最後までパースできたか返す."""
        return self.issue is None


def inspect_document(text: str) -> DocumentInspection:
    """文書を一度パースし、成功結果または人向けの問題を返す."""
    warnings = lint_parse_style(text)
    try:
        network = parse2net_uncached(text)
    except Exception as exc:  # noqa: BLE001 - parser errors are the result
        issue = exception_to_parse_issue(text, exc)
        errors = (issue,)
        if isinstance(exc, (MarkUncontainedError, QuotermNotFoundError)):
            errors = find_unresolved_reference_issues(text) or errors
        return DocumentInspection(
            network=None,
            errors=errors,
            warnings=warnings,
        )
    return DocumentInspection(network=network, errors=(), warnings=warnings)
