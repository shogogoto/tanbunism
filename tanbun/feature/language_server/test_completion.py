"""用語補完のテスト."""

from tanbun.feature.language_service import (
    complete_terms,
    extract_term_symbols,
)

DOCUMENT = """# title
    A |alpha, alpha alt: description
    beta: another description
    combined{beta}: composed term
"""


def test_extracts_names_aliases_and_composed_term_lookup_key() -> None:
    """用語名、同義名、alias、複合用語を抽出する."""
    symbols = extract_term_symbols(DOCUMENT)

    assert {symbol.label for symbol in symbols} == {
        "A",
        "alpha",
        "alpha alt",
        "beta",
        "combinedbeta",
    }
    assert next(symbol.detail for symbol in symbols if symbol.label == "A") == (
        "alias → alpha, alpha alt"
    )


def test_extracts_fullwidth_colon_definition() -> None:
    """全角コロンを使った定義も索引へ含める."""
    symbols = extract_term_symbols("# title\n  身体化\uff1a 体に覚えさせる\n")

    assert [symbol.label for symbol in symbols] == ["身体化"]


def test_completes_term_mark_and_adds_closing_brace() -> None:
    """{の後で用語を補完し、閉じ括弧も追加する."""
    text = DOCUMENT + "    sentence {alp"

    result = complete_terms(text, len(text))

    assert result is not None
    assert text[result.start_offset : result.end_offset] == "alp"
    assert result.closing == "}"
    assert "alpha" in {symbol.label for symbol in result.symbols}


def test_does_not_duplicate_existing_closing_brace() -> None:
    """すでにある閉じ括弧は重複させない."""
    suffix = "    sentence {alp}"
    text = DOCUMENT + suffix
    offset = len(text) - 1

    result = complete_terms(text, offset)

    assert result is not None
    assert not result.closing


def test_completes_quoterm_after_relation() -> None:
    """関係の後のquotermも補完する."""
    text = DOCUMENT + "    -> `bet"

    result = complete_terms(text, len(text))

    assert result is not None
    assert text[result.start_offset : result.end_offset] == "bet"
    assert result.closing == "`"
    assert "beta" in {symbol.label for symbol in result.symbols}


def test_does_not_complete_backtick_in_regular_sentence() -> None:
    """文の途中のbacktickをquotermと誤認しない."""
    text = DOCUMENT + "    regular sentence `bet"

    assert complete_terms(text, len(text)) is None
