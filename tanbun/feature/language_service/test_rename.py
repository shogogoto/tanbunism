"""名前変更は構文を保ち、同じ表記だけを編集案として返す."""
# ruff: noqa: PLR2004

import pytest

from . import RenameError, SourceDocument, prepare_rename, rename_term
from .position import DocumentPosition, position_to_offset


def apply_edits(document, edits):
    """共通サービスの編集案を後ろから適用して結果を比較する."""
    result = document.text
    for edit in reversed(edits):
        if edit.uri != document.uri:
            continue
        span = edit.source_range
        start = position_to_offset(
            document.text,
            DocumentPosition(span.line, span.start_character),
        )
        end = position_to_offset(
            document.text,
            DocumentPosition(span.line, span.end_character),
        )
        result = result[:start] + edit.new_text + result[end:]
    return result


def test_rename_definitions_and_exact_references_preserving_markup():
    """再定義、括弧・引用符・空白・全角コロンを保持し、平文は変更しない."""
    current = SourceDocument(
        "file:///current.tb",
        "# title\r\n  A | alpha, other\uff1aalphaの説明\r\n"
        "  { alpha } {alpha} {alphabet}\r\n  -> `alpha`\r\n  ! {alpha}\r\n",
    )
    other = SourceDocument(
        "file:///other.kn",
        "# other\n  alpha: 再定義\n  {alpha}\n  @author {alpha}\n",
    )
    offset = current.text.index("alpha,") + 1
    edits = rename_term(current, offset, "新版", (other,))
    assert apply_edits(current, edits) == current.text.replace(
        "alpha,",
        "新版,",
    ).replace("{ alpha }", "{ 新版 }").replace(
        "{alpha} {alphabet}",
        "{新版} {alphabet}",
    ).replace("`alpha`", "`新版`")
    assert (
        apply_edits(other, edits)
        == "# other\n  新版: 再定義\n  {新版}\n  @author {alpha}\n"
    )
    assert len(edits) == len({(edit.uri, edit.source_range) for edit in edits})


@pytest.mark.parametrize("name", ["A", "alpha", "synonym"])
def test_rename_alias_or_synonym_without_renaming_other_spellings(name):
    """alias・同義名は指定した表記だけを変える."""
    document = SourceDocument(
        "file:///a.tb",
        "# a\n  A | alpha, synonym: explanation\n  {A} {alpha} {synonym}\n",
    )
    edits = rename_term(document, document.text.index(name), "new", ())
    assert len(edits) == 2
    assert all(edit.new_text == "new" for edit in edits)


def test_prepare_on_reference_trims_only_name_and_finds_external_definition():
    """外部定義に対しても、括弧・余白を除いた名前が初期入力になる."""
    document = SourceDocument("file:///a.tb", "# a\n  😀{ 用語 }\n")
    other = SourceDocument("file:///b.tb", "# b\n  用語: 定義\n")
    target = prepare_rename(document, document.text.index("用語"), (other,))
    assert target.name == "用語"
    assert (
        document.text.splitlines()[target.source_range.line][
            target.source_range.start_character : target.source_range.end_character
        ]
        == "用語"
    )


def test_rename_embedded_component_updates_transitive_composed_references():
    """複合用語を構成する埋め込みと、派生した名前を指す参照も更新する."""
    document = SourceDocument(
        "file:///a.tb",
        "# a\n  元: 定義\n  大{前元}: 上位\n  前{元}: 複合\n"
        "  {前元} {大前元}\n  -> `大前元`\n",
    )
    edits = rename_term(document, document.text.index("元:"), "新")
    assert (
        apply_edits(document, edits)
        == "# a\n  新: 定義\n  大{前新}: 上位\n  前{新}: 複合\n"
        "  {前新} {大前新}\n  -> `大前新`\n"
    )


def test_rename_from_repeated_declaration_and_embedded_component():
    """再定義上や複合語内の参照から開始しても、元用語と全宣言を変更する."""
    document = SourceDocument(
        "file:///a.tb",
        "# a\n  元: 定義\n  元: 再定義\n  前{元}: 複合\n  {前元}\n",
    )
    for offset in [document.text.index("元: 再"), document.text.index("{元}") + 1]:
        edits = rename_term(document, offset, "新")
        assert apply_edits(document, edits) == document.text.replace("元", "新")


def test_rejects_two_derived_names_collapsing_to_the_same_name():
    """個別には未定義の変更先でも、派生語同士を同じ名前に潰さない."""
    document = SourceDocument(
        "file:///a.tb",
        "# a\n  A: 定義\n  {A}B: 複合1\n  B{A}: 複合2\n",
    )
    with pytest.raises(RenameError, match="同じ名前"):
        rename_term(document, document.text.index("A:"), "B")


@pytest.mark.parametrize(
    "name",
    [
        "",
        " leading",
        "trailing ",
        "a\nb",
        "{x}",
        "a|b",
        "a,b",
        "a:b",
        "a\uff1ab",
        "`x`",
        "# title",
        "-> other",
    ],
)
def test_rejects_names_that_change_tanbun_syntax(name):
    """構文を壊す入力は部分的な編集案も返さない."""
    document = SourceDocument("file:///a.tb", "# a\n  元: 定義\n")
    with pytest.raises(RenameError):
        rename_term(document, document.text.index("元"), name)


def test_rejects_alias_whitespace_and_existing_names_in_other_documents():
    """aliasの制約と、別ファイルの用語への衝突も検証する."""
    document = SourceDocument("file:///a.tb", "# a\n  A | alpha: 定義\n")
    other = SourceDocument("file:///b.tb", "# b\n  existing: 定義\n")
    with pytest.raises(RenameError, match="空白"):
        rename_term(document, document.text.index("A"), "two words")
    with pytest.raises(RenameError, match="既に"):
        rename_term(document, document.text.index("alpha"), "existing", (other,))


def test_rejects_composed_name_collisions():
    """派生名の衝突も編集前に拒否する."""
    document = SourceDocument(
        "file:///a.tb",
        "# a\n  元: 定義\n  前{元}: 複合\n  前新: 既存\n",
    )
    with pytest.raises(RenameError, match="既に"):
        rename_term(document, document.text.index("元:"), "新")


def test_prepare_rejects_plain_text_unknown_references_and_composed_whole_name():
    """未定義・平文・複合用語全体の破壊的な上書きは対象にしない."""
    document = SourceDocument(
        "file:///a.tb",
        "# a\n  元: 定義\n  前{元}: 複合\n  {前元} {missing}\n  plain\n",
    )
    for value in ["# a", "plain", "missing", "前元}"]:
        assert prepare_rename(document, document.text.index(value)) is None
    assert rename_term(document, document.text.index("元:"), "元") == ()
