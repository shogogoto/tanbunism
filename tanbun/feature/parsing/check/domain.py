"""Tanbun文書をDBに依存せず検査する."""

from __future__ import annotations

from dataclasses import dataclass

from tanbun.feature.parsing.issue import (
    ParseIssue,
    exception_to_parse_issue,
    lint_parse_style,
)
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.tree2net import parse2net_uncached


@dataclass(frozen=True)
class DocumentInspection:
    """文書のパース結果."""

    network: SysNet | None
    issue: ParseIssue | None
    warnings: tuple[ParseIssue, ...]

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
        return DocumentInspection(
            network=None,
            issue=exception_to_parse_issue(text, exc),
            warnings=warnings,
        )
    return DocumentInspection(network=network, issue=None, warnings=warnings)
