"""用語参照検索のテスト."""

from tanbun.feature.language_service import (
    ReferenceKind,
    SourceDocument,
    find_references,
    group_references,
)


def test_finds_embedded_and_quoterm_references_across_documents() -> None:
    """埋め込み用語とquotermを区別したまま複数文書から探す."""
    current = SourceDocument(
        "file:///current.tb",
        "# current\n  共有用語: 説明\n  文中の{共有用語}\n",
    )
    other = SourceDocument(
        "file:///other.kn",
        "# other\n  対象\n    <- `共有用語`\n",
    )
    offset = current.text.index("共有用語:") + 2

    result = find_references(current, offset, (other,))

    assert [(item.uri, item.kind) for item in result] == [
        (current.uri, ReferenceKind.EMBEDDED_TERM),
        (other.uri, ReferenceKind.QUOTERM),
    ]


def test_optionally_includes_declaration() -> None:
    """LSPのincludeDeclarationに応じて定義宣言も返す."""
    current = SourceDocument(
        "file:///current.tb",
        "# current\n  用語: 説明\n  {用語}\n",
    )
    offset = current.text.index("{用語}") + 2

    result = find_references(current, offset, include_declaration=True)

    assert [item.kind for item in result] == [
        ReferenceKind.EMBEDDED_TERM,
        ReferenceKind.DEFINITION,
    ]


def test_groups_references_by_kind() -> None:
    """Web表示用に参照を記法別のまとまりへ変換する."""
    current = SourceDocument(
        "file:///current.tb",
        "# current\n  用語: 説明\n  `用語`\n  {用語}\n",
    )
    offset = current.text.index("用語:") + 1

    groups = group_references(find_references(current, offset))

    assert [group.kind for group in groups] == [
        ReferenceKind.EMBEDDED_TERM,
        ReferenceKind.QUOTERM,
    ]
