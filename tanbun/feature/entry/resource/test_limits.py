"""Resourceアップロード上限のテスト."""

import pytest

from tanbun.feature.entry.errors import ResourceUploadLimitError
from tanbun.feature.entry.resource.limits import (
    ResourceUploadLimits,
    validate_batch_size,
    validate_file_count,
    validate_file_size,
    validate_resource_text,
)

LIMITS = ResourceUploadLimits(
    max_files=2,
    max_file_bytes=5,
    max_batch_bytes=8,
)


def test_accept_resource_upload_at_limits() -> None:
    """上限と同じ処理量は受け入れる."""
    validate_file_count(2, LIMITS)
    validate_file_size(5, LIMITS)
    validate_batch_size(8, LIMITS)


def test_reject_too_many_files() -> None:
    """ファイル数の超過を拒否する."""
    with pytest.raises(ResourceUploadLimitError):
        validate_file_count(3, LIMITS)


def test_reject_too_large_file() -> None:
    """単一ファイルの超過を拒否する."""
    with pytest.raises(ResourceUploadLimitError):
        validate_file_size(6, LIMITS)


def test_reject_too_large_batch() -> None:
    """合計サイズの超過を拒否する."""
    with pytest.raises(ResourceUploadLimitError):
        validate_batch_size(9, LIMITS)


def test_resource_text_limit_counts_utf8_bytes() -> None:
    """文字数ではなく送信時に近いUTF-8バイト数で制限する."""
    with pytest.raises(ResourceUploadLimitError):
        validate_resource_text("ああ", LIMITS)
