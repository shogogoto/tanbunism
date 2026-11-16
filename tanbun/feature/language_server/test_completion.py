"""用語補完のテスト."""

from lsprotocol import types

from tanbun.feature.language_service import (
    complete_terms,
    extract_term_symbols,
)

from .server import create_server

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


def test_lsp_metadata_trigger_and_completion_edit(tmp_path) -> None:
    """@をトリガーとして公開し、実際のLSP応答でキーを補完する."""
    server = create_server()
    list(
        server.protocol.lsp_initialize(
            types.InitializeParams(
                capabilities=types.ClientCapabilities(),
                root_uri=tmp_path.as_uri(),
            ),
        ),
    )
    uri = (tmp_path / "source.tb").as_uri()
    server.workspace.put_text_document(
        types.TextDocumentItem(
            uri=uri,
            language_id="tanbun",
            version=1,
            text="# title\n  @au",
        ),
    )

    assert (
        "@"
        in server.protocol.server_capabilities.completion_provider.trigger_characters
    )
    response = server.protocol.fm.features[types.TEXT_DOCUMENT_COMPLETION](
        types.CompletionParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=1, character=len("  @au")),
        ),
    )

    assert [item.label for item in response.items] == ["author", "published", "url"]
    author = response.items[0]
    assert author.kind == types.CompletionItemKind.Property
    assert author.text_edit == types.TextEdit(
        range=types.Range(
            start=types.Position(line=1, character=len("  @")),
            end=types.Position(line=1, character=len("  @au")),
        ),
        new_text="author ",
    )

    # 既存の用語補完も、改行なしの最終行とUTF-16文字位置を保つ。
    server.workspace.put_text_document(
        types.TextDocumentItem(
            uri=uri,
            language_id="tanbun",
            version=2,
            text="# title\n  alpha: description\n  😀{al",
        ),
    )
    terms = server.protocol.fm.features[types.TEXT_DOCUMENT_COMPLETION](
        types.CompletionParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=2, character=len("  😀{al") + 1),
        ),
    )
    assert terms.items[0].kind == types.CompletionItemKind.Reference
    assert terms.items[0].text_edit == types.TextEdit(
        range=types.Range(
            start=types.Position(line=2, character=len("  😀{") + 1),
            end=types.Position(line=2, character=len("  😀{al") + 1),
        ),
        new_text="alpha}",
    )
