"""Language Serverの診断テスト."""

from unittest.mock import MagicMock

import pytest
from lsprotocol import types
from pygls.workspace import PositionCodec

from tanbun.feature.language_server.server import (
    _report_analysis,
    _to_lsp_diagnostic,
    create_server,
)
from tanbun.feature.language_service import analyze, diagnose
from tanbun.feature.parsing.issue import SourceRange


def test_server_advertises_definition_support() -> None:
    """clientにdefinitionとreferences capabilityを公開する."""
    server = create_server()

    assert types.TEXT_DOCUMENT_DEFINITION in server.protocol.fm.features
    assert types.TEXT_DOCUMENT_REFERENCES in server.protocol.fm.features


def test_valid_document_has_no_diagnostics() -> None:
    """正常な読書メモに診断を出さない."""
    assert diagnose("# title\n    sentence\n") == []


def test_analysis_reports_parse_time_and_graph_statistics() -> None:
    """正常な文書の解析時間とグラフ規模を返す."""
    text = "# title\n    term: sentence\n"
    analysis = analyze(text)

    statistics = analysis.statistics
    assert statistics.duration_ms >= 0
    assert statistics.line_count == len(text.splitlines())
    assert statistics.term_count == 1
    assert statistics.node_count is not None
    assert statistics.relation_count is not None


def test_analysis_keeps_available_statistics_on_error() -> None:
    """解析失敗時も所要時間と行数を返す."""
    analysis = analyze("invalid\n")

    assert analysis.diagnostics
    assert analysis.statistics.duration_ms >= 0
    assert analysis.statistics.line_count == 1
    assert analysis.statistics.node_count is None


def test_reports_syntax_error_in_plain_language() -> None:
    """構文エラーを内部トークンではなく人向けに説明する."""
    [diagnostic] = diagnose("title invalid\n")

    assert diagnostic.code == "missing-title"
    assert diagnostic.source_range.line == 0
    assert diagnostic.source_range.start_character == 0
    assert diagnostic.message == "文書は「# タイトル」から始めてください。"


def test_reports_unindented_body_with_fix_and_full_line_range() -> None:
    """見出し直下のインデント不足を修正例付きで説明する."""
    text = "# title\n\n## section\n経験説: そう知ってるからそう知覚する\n"

    [diagnostic] = diagnose(text)

    assert diagnostic.message == "本文が見出しと同じ深さにあります。"
    assert diagnostic.suggestion == (
        "行頭にスペースを追加 (例: 「  経験説: そう知ってるからそう知覚する」)"
    )
    assert diagnostic.source_range == SourceRange(
        line=3,
        start_character=0,
        end_character=len("経験説: そう知ってるからそう知覚する"),
    )


def test_reports_skipped_heading_level_with_fix() -> None:
    """飛び越した見出しをLarkのトークン名なしで説明する."""
    text = "# title\n### skipped\n"

    [diagnostic] = diagnose(text)

    assert diagnostic.code == "heading-level-mismatch"
    assert diagnostic.message == "見出しレベルが飛んでいます。"
    assert diagnostic.suggestion == "「## skipped」へ変更"
    assert "UnexpectedToken" not in diagnostic.display_message()
    assert "H2" not in diagnostic.display_message()


def test_undefined_quoterm_points_to_backtick_reference() -> None:
    """同じ名前を含む後続用語でなく未定義のbacktickを指す."""
    text = (
        "# title\n"
        "  論理的明証性: 論理による明証性\n"
        "  前提\n"
        "    <- `明証性`\n"
        "  事実の明証性: 観察による明証性\n"
        "  感覚意識の明証性: 心に関する{事実の明証性}\n"
    )

    [diagnostic] = diagnose(text)

    assert diagnostic.code == "undefined-quoterm"
    assert diagnostic.message == "引用用語「明証性」が定義されていません。"
    assert diagnostic.source_range == SourceRange(
        line=3,
        start_character=len("    <- `"),
        end_character=len("    <- `明証性"),
    )
    assert "明証性: ..." in (diagnostic.suggestion or "")


def test_fullwidth_definition_colon_is_warning() -> None:
    """全角コロンを受理しつつWarningとして半角への統一を案内する."""
    text = "# title\n  身体化\uff1a 体に覚えさせる\n"

    [diagnostic] = diagnose(text)
    converted = _to_lsp_diagnostic(
        text,
        diagnostic,
        PositionCodec(types.PositionEncodingKind.Utf8),
    )

    assert diagnostic.code == "noncanonical-definition-separator"
    assert converted.severity is types.DiagnosticSeverity.Warning
    assert "半角コロン" in diagnostic.display_message()


def test_reports_uncontained_mark_at_mark_position() -> None:
    """未定義用語を参照した場所を返す."""
    text = "# title\n    sentence {missing}\n"

    [diagnostic] = diagnose(text)

    assert diagnostic.code == "undefined-term"
    assert diagnostic.source_range == SourceRange(
        line=1,
        start_character=len("    sentence "),
        end_character=len("    sentence {missing}"),
    )


@pytest.mark.parametrize(
    ("encoding", "expected_character"),
    [
        (types.PositionEncodingKind.Utf8, len("    💡 ".encode())),
        (types.PositionEncodingKind.Utf16, len("    💡 ".encode("utf-16-le")) // 2),
    ],
)
def test_lsp_position_uses_negotiated_code_units(
    encoding: types.PositionEncodingKind,
    expected_character: int,
) -> None:
    """クライアントと合意した文字エンコードの位置へ変換する."""
    text = "# title\n    💡 {missing}\n"
    [diagnostic] = diagnose(text)

    converted = _to_lsp_diagnostic(
        text,
        diagnostic,
        PositionCodec(encoding),
    )

    assert converted.range.start.character == expected_character
    assert converted.code is None


def test_reports_visible_ready_message_with_statistics() -> None:
    """準備完了時に解析統計を表示とログの両方へ送る."""
    server = MagicMock()
    analysis = analyze("# title\n    term: sentence\n")

    _report_analysis(server, analysis, visible=True)

    log_params = server.window_log_message.call_args.args[0]
    show_params = server.window_show_message.call_args.args[0]
    assert "準備完了" in log_params.message
    assert "ms" in log_params.message
    assert "2行" in log_params.message
    assert "1用語" in log_params.message
    assert show_params.message == log_params.message
