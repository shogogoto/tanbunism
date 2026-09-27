"""用語の定義検索テスト."""

from tanbun.feature.language_service import (
    SourceDocument,
    find_definition,
    find_definitions,
)
from tanbun.feature.parsing.issue import SourceRange


def _offset_of(text: str, value: str) -> int:
    return text.index(value) + len(value) // 2


def test_finds_embedded_term_definition() -> None:
    """{用語}から同じ文書の定義へ移動する."""
    text = "# title\n  身体化\uff1a 体に覚えさせる\n  文中の{身体化}を参照\n"

    result = find_definition(text, _offset_of(text, "{身体化}"))

    assert result == SourceRange(line=1, start_character=2, end_character=5)


def test_finds_quoterm_definition() -> None:
    """関係のquotermから同じ文書の定義へ移動する."""
    text = "# title\n  イスラーム史観: 説明\n  対象\n    <- `イスラーム史観`\n"

    result = find_definition(text, _offset_of(text, "`イスラーム史観`"))

    assert result == SourceRange(line=1, start_character=2, end_character=9)


def test_alias_points_to_alias_definition() -> None:
    """alias参照はaliasが宣言された位置へ移動する."""
    text = "# title\n  心理 | psychology: 説明\n  {心理}\n"

    result = find_definition(text, _offset_of(text, "{心理}"))

    assert result == SourceRange(line=1, start_character=2, end_character=4)


def test_definition_works_while_rest_of_document_is_invalid() -> None:
    """編集中の文書全体が不正でもraw textの索引から定義を探す."""
    text = "# title\n  用語: 説明\ninvalid line\n  {用語}\n"

    assert find_definition(text, _offset_of(text, "{用語}")) is not None


def test_returns_none_outside_reference_or_for_unknown_term() -> None:
    """参照外または未定義なら移動先を返さない."""
    text = "# title\n  defined: 説明\n  {missing}\n"

    assert find_definition(text, 0) is None
    assert find_definition(text, _offset_of(text, "{missing}")) is None


def test_finds_definition_in_another_local_document() -> None:
    """現在の文書にない用語は別のローカル文書から探す."""
    current = SourceDocument("file:///current.tb", "# current\n  {共有用語}\n")
    other = SourceDocument("file:///source.tb", "# source\n  共有用語: 説明\n")

    result = find_definitions(
        current,
        _offset_of(current.text, "{共有用語}"),
        [other],
    )

    assert result[0].uri == other.uri
    assert result[0].source_range == SourceRange(
        line=1,
        start_character=2,
        end_character=6,
    )


def test_prefers_definition_in_current_document() -> None:
    """同名用語が別文書にもあれば現在の文書内定義を優先する."""
    current = SourceDocument(
        "file:///current.tb",
        "# current\n  用語: current\n  {用語}\n",
    )
    other = SourceDocument("file:///other.tb", "# other\n  用語: other\n")

    result = find_definitions(
        current,
        _offset_of(current.text, "{用語}"),
        [other],
    )

    assert [location.uri for location in result] == [current.uri]
