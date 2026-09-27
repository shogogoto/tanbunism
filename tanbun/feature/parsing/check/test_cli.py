"""Tanbun文書検査CLIのテスト."""

from pathlib import Path

from click.testing import CliRunner

from tanbun.feature.parsing.check.cli import check_cmd


def test_check_valid_file() -> None:
    """正常なファイルでは成功する."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("valid.tb").write_text("# title\n  body\n", encoding="utf-8")

        result = runner.invoke(check_cmd, ["valid.tb"])

    assert result.exit_code == 0
    assert "1ファイルを検査: 1正常, 0エラー" in result.output


def test_check_lists_only_invalid_file_paths_by_default() -> None:
    """通常表示ではエラーがあるファイルのパスだけを列挙する."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("invalid.kn").write_text(
            "# title\n\n## section\nbody\n",
            encoding="utf-8",
        )

        result = runner.invoke(check_cmd, ["invalid.kn"])

    assert result.exit_code == 1
    assert result.output.splitlines() == [
        "invalid.kn",
        "1ファイルを検査: 0正常, 1エラー",
    ]


def test_check_verbose_reports_actionable_error_with_source() -> None:
    """詳細表示では人向けの説明、ソース行、修正案を表示する."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("invalid.kn").write_text(
            "# title\n\n## section\nbody\n",
            encoding="utf-8",
        )

        result = runner.invoke(check_cmd, ["invalid.kn", "-v"])

    assert result.exit_code == 1
    assert "invalid.kn:4:1: 本文が見出しと同じ深さにあります。" in result.output
    assert "4 | body" in result.output
    assert "修正案: 行頭にスペースを追加" in result.output
    assert "UnexpectedToken" not in result.output
    assert "1ファイルを検査: 0正常, 1エラー" in result.output


def test_check_verbose_explains_relation_without_source_sentence() -> None:
    """見出し直下の関係行をPydantic内部エラーにしない."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("invalid.kn").write_text(
            "# title\n  parent\n  <-> child\n",
            encoding="utf-8",
        )

        result = runner.invoke(check_cmd, ["invalid.kn", "-v"])

    assert result.exit_code == 1
    assert "invalid.kn:3:3: 関係行に接続元の単文がありません。" in result.output
    assert "3 |   <-> child" in result.output
    assert "この行をさらにインデント" in result.output
    assert "validation errors for DirectedEdge" not in result.output


def test_check_directory_recursively_and_filter_extensions() -> None:
    """ディレクトリでは指定拡張子だけを再帰検査する."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("notes/child").mkdir(parents=True)
        Path("notes/valid.kn").write_text("# valid\n", encoding="utf-8")
        Path("notes/child/invalid.tb").write_text("invalid\n", encoding="utf-8")
        Path("notes/ignored.md").write_text("invalid\n", encoding="utf-8")

        result = runner.invoke(check_cmd, ["notes"])

    assert result.exit_code == 1
    assert "child/invalid.tb" in result.output
    assert "child/invalid.tb:1:1" not in result.output
    assert "ignored.md" not in result.output
    assert "2ファイルを検査: 1正常, 1エラー" in result.output


def test_check_custom_extension() -> None:
    """対象拡張子を差し替えられる."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("notes").mkdir()
        Path("notes/invalid.md").write_text("invalid\n", encoding="utf-8")
        Path("notes/ignored.kn").write_text("invalid\n", encoding="utf-8")

        result = runner.invoke(
            check_cmd,
            ["notes", "--extension", "md"],
        )

    assert result.exit_code == 1
    assert "invalid.md" in result.output
    assert "ignored.kn" not in result.output
    assert "1ファイルを検査" in result.output
