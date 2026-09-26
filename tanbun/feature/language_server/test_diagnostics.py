"""Language Serverの診断テスト."""

from tanbun.feature.language_server.diagnostics import SourceRange, diagnose
from tanbun.feature.language_server.server import _to_lsp_diagnostic


def test_valid_document_has_no_diagnostics() -> None:
    """正常な読書メモに診断を出さない."""
    assert diagnose("# title\n    sentence\n") == []


def test_reports_syntax_error_position_and_expected_tokens() -> None:
    """構文エラーの位置と期待トークンを返す."""
    [diagnostic] = diagnose("title invalid\n")

    assert diagnostic.code == "UnexpectedToken"
    assert diagnostic.source_range.line == 0
    assert diagnostic.source_range.start_character == 0
    assert "Expected:" in diagnostic.message
    assert "H1" in diagnostic.message


def test_reports_uncontained_mark_at_mark_position() -> None:
    """未定義用語を参照した場所を返す."""
    text = "# title\n    sentence {missing}\n"

    [diagnostic] = diagnose(text)

    assert diagnostic.code == "MarkUncontainedError"
    assert diagnostic.source_range == SourceRange(
        line=1,
        start_character=len("    sentence "),
        end_character=len("    sentence {missing}"),
    )


def test_lsp_position_uses_utf16_code_units() -> None:
    """LSPで要求されるUTF-16位置へ変換する."""
    text = "# title\n    💡 {missing}\n"
    [diagnostic] = diagnose(text)

    converted = _to_lsp_diagnostic(text, diagnostic)

    assert converted.range.start.character == (
        diagnostic.source_range.start_character + 1
    )
