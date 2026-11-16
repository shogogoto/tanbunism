"""メタデータキー補完の文脈と置換範囲の検証."""

import pytest

from .completion import complete_metadata


@pytest.mark.parametrize("prefix", ["@", "  @", "\t@pub", "# 日本語\r\n  @au"])
def test_metadata_completion_after_marker(prefix: str) -> None:
    """行頭の@と入力途中のキーから候補を取得する."""
    result = complete_metadata(prefix, len(prefix))

    assert result is not None
    assert [item.label for item in result.symbols] == ["author", "published", "url"]
    assert result.start_offset == prefix.rfind("@") + 1
    assert result.end_offset == len(prefix)
    assert result.closing == " "


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  @au", "  @author "),
        ("  @au John", "  @author John"),
        ("  @au: John", "  @author: John"),
        ("  @author John", "  @author John"),
    ],
)
def test_metadata_edit_preserves_values_and_existing_separators(
    text: str,
    expected: str,
) -> None:
    """@や既存の値・区切りを壊さず、キーの途中からも置換する."""
    result = complete_metadata(text, text.index("@") + len("@au"))

    assert result is not None
    replacement = "author" + result.closing
    assert (
        text[: result.start_offset] + replacement + text[result.end_offset :]
        == expected
    )


@pytest.mark.parametrize(
    "text",
    ["sentence @", "  ! @", "# @", "  @author ", "  @published 2026", "  @url: "],
)
def test_metadata_completion_does_not_trigger_in_prose_or_values(text: str) -> None:
    """文中・コメント・見出し・メタデータの値では候補を出さない."""
    assert complete_metadata(text, len(text)) is None
