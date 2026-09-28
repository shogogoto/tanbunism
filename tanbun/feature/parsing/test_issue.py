"""WebとLSPで共有するパース問題のテスト."""

import pytest

from tanbun.feature.domain.errors import DomainError
from tanbun.feature.parsing.check.domain import inspect_document
from tanbun.feature.parsing.domain import try_parse2net
from tanbun.feature.parsing.issue import (
    SourceRange,
    exception_to_parse_issue,
    lint_parse_style,
)
from tanbun.feature.parsing.primitive.mark.errors import MarkContainsMarkError
from tanbun.feature.parsing.primitive.term.errors import TermConflictError
from tanbun.feature.parsing.sysnet.errors import SentenceConflictError
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


def test_invalid_indent_points_to_fullwidth_space_root_cause() -> None:
    """後続行で発覚したindentエラーを先行する全角スペースへ戻す."""
    text = "# title\n  \u3000first\n      child\n    sibling\n"

    issue = exception_to_parse_issue(
        text,
        UndedentError("Invalid indent was detected at line 4."),
    )

    assert issue.code == "non-ascii-indent"
    assert issue.source_range == SourceRange(
        line=1,
        start_character=2,
        end_character=3,
    )
    assert "全角スペース" in issue.message


def test_orphan_relation_with_term_target_points_to_relation_line() -> None:
    """用語だけの関係先も、内部表現でなく原文の関係行へ戻す."""
    text = "# title\n  first\n  -> relation target, alias:\n"

    issue = inspect_document(text).issue

    assert issue is not None
    assert issue.code == "orphan-relation"
    assert issue.source_range == SourceRange(
        line=2,
        start_character=2,
        end_character=len("  -> relation target, alias:"),
    )
    assert "関係記号を削除" in (issue.suggestion or "")


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


def test_invalid_time_points_to_value_with_plain_language_fix() -> None:
    """EDTF内部エラーでなく不正な日時の位置と修正例を示す."""
    text = "# title\n  event\n    when. 187Q ~ 1900\n"

    issue = inspect_document(text).issue

    assert issue is not None
    assert issue.code == "invalid-time-expression"
    assert issue.message == "日時・期間「187Q」を解釈できません。"
    assert issue.suggestion == "対応している日時または「開始 ~ 終了」形式の期間へ変更"
    assert issue.source_range == SourceRange(
        line=2,
        start_character=len("    when. "),
        end_character=len("    when. 187Q"),
    )


def test_time_season_error_points_to_when_value() -> None:
    """独自日時エラーも文書先頭でなく原因となったwhen値を指す."""
    text = "# title\n  event\n    when. 1970 EARLY\n"

    issue = inspect_document(text).issue

    assert issue is not None
    assert issue.code == "invalid-time-expression"
    assert issue.message == "日時・期間「1970 EARLY」を解釈できません。"
    assert "世紀表記にだけ使用できます" in (issue.suggestion or "")
    assert "1970 ~ 1973" in (issue.suggestion or "")
    assert issue.source_range == SourceRange(
        line=2,
        start_character=len("    when. "),
        end_character=len("    when. 1970 EARLY"),
    )


def test_duplicate_term_points_to_later_definition() -> None:
    """用語名の前に説明がある例外でも重複した側を指す."""
    text = "# title\n  ヌーメン: first\n  ヌーメン: second\n"

    issue = exception_to_parse_issue(
        text,
        TermConflictError("用語'ヌーメン'が重複しています"),
    )

    assert issue.source_range == SourceRange(
        line=2,
        start_character=2,
        end_character=6,
    )
    assert issue.code == "duplicate-term"
    assert "最初の定義は2行目" in issue.message


def test_alias_with_term_reference_points_to_alias() -> None:
    """aliasに波括弧がある場合は文書先頭でなくaliasを指す."""
    text = "# title\n  bad{alias} | sentence\n"

    issue = inspect_document(text).issue

    assert issue is not None
    assert issue.code == "invalid-alias"
    assert issue.source_range == SourceRange(
        line=1,
        start_character=2,
        end_character=len("  bad{alias}"),
    )
    assert "bad{alias}" in issue.message


def test_duplicate_sentence_points_to_exact_later_line() -> None:
    """部分一致する後続文でなく、重複した単文そのものを指す."""
    text = "# title\n  same\n  same\n  sentence ending same\n"

    issue = exception_to_parse_issue(
        text,
        SentenceConflictError("'same'は重複しています", sentence="same"),
    )

    assert issue.code == "duplicate-sentence"
    assert issue.source_range == SourceRange(line=2, start_character=2, end_character=6)
    assert "最初の単文は2行目" in issue.message


def test_duplicate_term_does_not_point_to_later_reference() -> None:
    """重複定義の診断を、後方の{} 参照に出さない."""
    text = (
        "# title\n"
        "  構造言語学: first\n"
        "  {構造言語学}のやり方\n"
        "  構造言語学: second\n"
        "    <-> {構造言語学}では扱えない\n"
    )

    issue = exception_to_parse_issue(
        text,
        TermConflictError("用語'構造言語学'が重複しています"),
    )

    assert issue.source_range == SourceRange(
        line=3,
        start_character=2,
        end_character=7,
    )
    assert "最初の定義は2行目" in issue.message


def test_duplicate_term_with_multiple_names_points_to_definition() -> None:
    """複数名を持つ用語でも、2個目の定義を指す."""
    text = (
        "# title\n"
        "  音声学, phonetics:\n"
        "  {音声学}に言及\n"
        "  音声学, phonetics: 音声が作られる生理学\n"
    )

    issue = exception_to_parse_issue(
        text,
        TermConflictError(
            "用語'音声学(phonetics)'が重複しています",
            names=("音声学", "phonetics"),
        ),
    )

    assert issue.source_range == SourceRange(
        line=3,
        start_character=2,
        end_character=5,
    )
    assert issue.message == (
        "用語「音声学、phonetics」が複数回定義されています。 最初の定義は2行目です。"
    )


def test_nested_mark_points_to_source_instead_of_document_start() -> None:
    """文字として書かれた入れ子波括弧の位置と修正法を示す."""
    source = "形態素はカッコ{{}} で囲む"
    text = f"# title\n  section\n    {source}\n"

    issue = exception_to_parse_issue(
        text,
        MarkContainsMarkError("internal parser error", source=source),
    )

    assert issue.code == "nested-term-reference"
    assert issue.source_range == SourceRange(
        line=2,
        start_character=len("    形態素はカッコ"),
        end_character=len("    形態素はカッコ{{"),
    )
    assert "入れ子にできません" in issue.message
    assert "2つ重ねる" in (issue.suggestion or "")
