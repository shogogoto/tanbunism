"""同期的なResource保存処理の負荷上限."""

from dataclasses import dataclass

from tanbun.feature.entry.errors import ResourceUploadLimitError


@dataclass(frozen=True, slots=True)
class ResourceUploadLimits:
    """一回の同期アップロードで許容する処理量."""

    max_files: int = 20
    max_file_bytes: int = 2 * 1024 * 1024
    max_batch_bytes: int = 10 * 1024 * 1024


DEFAULT_RESOURCE_UPLOAD_LIMITS = ResourceUploadLimits()


def validate_file_count(
    count: int,
    limits: ResourceUploadLimits = DEFAULT_RESOURCE_UPLOAD_LIMITS,
) -> None:
    """一度に保存するファイル数を検証."""
    if count > limits.max_files:
        msg = f"一度にアップロードできるResourceは{limits.max_files}件までです"
        raise ResourceUploadLimitError(msg=msg)


def validate_file_size(
    size: int,
    limits: ResourceUploadLimits = DEFAULT_RESOURCE_UPLOAD_LIMITS,
) -> None:
    """単一ファイルのバイト数を検証."""
    if size > limits.max_file_bytes:
        msg = f"1つのResourceは{limits.max_file_bytes}バイトまでです"
        raise ResourceUploadLimitError(msg=msg)


def validate_batch_size(
    size: int,
    limits: ResourceUploadLimits = DEFAULT_RESOURCE_UPLOAD_LIMITS,
) -> None:
    """一度に保存するファイルの合計バイト数を検証."""
    if size > limits.max_batch_bytes:
        msg = (
            "一度にアップロードできる合計サイズは"
            f"{limits.max_batch_bytes}バイトまでです"
        )
        raise ResourceUploadLimitError(msg=msg)


def validate_resource_text(
    text: str,
    limits: ResourceUploadLimits = DEFAULT_RESOURCE_UPLOAD_LIMITS,
) -> None:
    """テキスト入力をUTF-8で送信した場合のサイズを検証."""
    validate_file_size(len(text.encode()), limits)
