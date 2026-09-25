"""Entryドメインのテスト."""

from tanbun.feature.entry.domain import resource_text_hash

EXPECTED_HASH = 5258810334303206837


def test_resource_text_hash_is_stable() -> None:
    """本文ハッシュはPythonプロセスに依存しない."""
    assert resource_text_hash("# title\n  memo\n") == EXPECTED_HASH
