"""WebとLSPで共有するパース問題のテスト."""

import pytest

from tanbun.feature.domain.errors import DomainError
from tanbun.feature.parsing.domain import try_parse2net
from tanbun.feature.parsing.issue import exception_to_parse_issue, lint_parse_style
from tanbun.feature.parsing.tree_parse.errors import UndedentError


def test_try_parse2net_reports_actionable_message() -> None:
    """Web APIへ伝わるDomainErrorにも原因と修正案を含める."""
    text = "# title\n\n## section\n経験説: そう知ってるからそう知覚する\n"

    with pytest.raises(DomainError) as caught:
        try_parse2net(text)

    message = caught.value.detail["message"]
    assert "4行目: 本文が見出しと同じ深さにあります。" in message
    assert "行頭にスペースを追加" in message
    assert "UnexpectedToken" not in message


def test_try_parse2net_hides_lark_token_names() -> None:
    """Web API用メッセージにLarkの例外名やトークン名を出さない."""
    with pytest.raises(DomainError) as caught:
        try_parse2net("# title\n### skipped\n")

    message = caught.value.detail["message"]
    assert "見出しレベルが飛んでいます。" in message
    assert "「## skipped」へ変更" in message
    assert "UnexpectedToken" not in message
    assert "H2" not in message


def test_invalid_indent_has_plain_language_explanation() -> None:
    """インデント検出器の内部メッセージを人向けに置き換える."""
    text = "# title\n  first\n second\n"

    issue = exception_to_parse_issue(
        text,
        UndedentError("Invalid indent was detected at line 3."),
    )

    assert issue.code == "invalid-indent"
    assert issue.message == "インデントの深さが前後の行と一致していません。"
    assert "スペース数を変更" in (issue.suggestion or "")
    assert "Invalid indent" not in issue.display_message()


def test_fullwidth_definition_separator_is_a_warning() -> None:
    """受理する全角コロンには半角への統一を案内する."""
    [issue] = lint_parse_style("# title\n  身体化\uff1a 説明\n")

    assert issue.code == "noncanonical-definition-separator"
    assert issue.severity == "warning"
    assert issue.source_range.line == 1
    assert issue.message == "定義区切りに全角コロンが使われています。"
    assert issue.suggestion == "半角コロン「:」へ変更"


def test_fullwidth_colon_in_metadata_is_not_a_warning() -> None:
    """用語定義として解釈しない行は警告しない."""
    text = "# 時刻\uff1a正午\n  @url https\uff1a//example.com\n  when. 10\uff1a30\n"

    assert lint_parse_style(text) == ()
