"""互換用import。診断処理は共通language serviceに置く."""

from tanbun.feature.language_service.analysis import (
    DocumentAnalysis,
    ParseStatistics,
    analyze,
    diagnose,
)

__all__ = ["DocumentAnalysis", "ParseStatistics", "analyze", "diagnose"]
