"""Language Serverの診断テスト."""

import pytest
from lsprotocol import types
from pygls.workspace import PositionCodec

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
