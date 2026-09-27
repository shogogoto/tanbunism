"""未定義参照の一括診断テスト."""

from tanbun.feature.parsing.check.domain import inspect_document
from tanbun.feature.parsing.check.unresolved import (
    find_unresolved_reference_issues,
)


def test_finds_every_unresolved_reference_with_frequency() -> None:
    """最初の1件で止まらず、全参照の位置と頻度を返す."""
    text = (
        "# title\n"
        "  構造言語学: defined\n"
        "  {構造主義}と{構造言語学}\n"
        "  `構造主義`\n"
        "  {未定義}\n"
    )

    issues = find_unresolved_reference_issues(text)

    assert [issue.source_range.line for issue in issues] == [2, 3, 4]
    assert [issue.code for issue in issues] == [
        "undefined-term",
        "undefined-quoterm",
        "undefined-term",
    ]
    assert "2箇所で参照" in issues[0].message
    assert "構造言語学" in (issues[0].suggestion or "")


def test_ignores_escaped_braces_and_formulae() -> None:
    """文字と数式の波括弧を用語参照にしない."""
    text = "# title\n  literal {{name}} and ${formula}$\n"

    assert find_unresolved_reference_issues(text) == ()


def test_inline_code_is_not_a_quoterm_reference() -> None:
    """文中のbacktick表記を、単独行の引用用語と混同しない."""
    text = "# title\n  文中の `code` は単なる表記\n"

    assert find_unresolved_reference_issues(text) == ()


def test_inspection_returns_all_unresolved_references() -> None:
    """CLI・LSP・Web共通の検査結果に全件を載せる."""
    text = "# title\n  {不足A}\n  {不足B}\n"

    inspection = inspect_document(text)

    assert inspection.network is None
    assert [issue.message for issue in inspection.errors] == [
        "用語「不足A」が定義されていません。",
        "用語「不足B」が定義されていません。",
    ]
    assert inspection.issue == inspection.errors[0]
