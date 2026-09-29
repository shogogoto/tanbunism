"""管理者向けメンテナンスAPIのモデル."""

from enum import StrEnum, auto

from pydantic import BaseModel, Field


class OrphanReason(StrEnum):
    """単文から本来の配置を解決できない理由."""

    MISSING_RESOURCE = auto()
    MISSING_OWNER = auto()
    MISSING_LOCATION = auto()


class OrphanedTanbun(BaseModel, frozen=True):
    """Resource内の配置を失った現行単文."""

    uid: str
    sentence: str
    resource_uid: str | None
    resource_name: str | None
    owner_email: str | None
    reason: OrphanReason
    quiz_reference_count: int
    answer_reference_count: int
    relationship_count: int


class DeleteOrphanedTanbunsRequest(BaseModel, frozen=True):
    """削除対象の孤立単文."""

    sentence_ids: list[str] = Field(min_length=1, max_length=500)


class DeleteOrphanedTanbunsResult(BaseModel, frozen=True):
    """孤立単文の掃除結果."""

    deleted_count: int
    retired_count: int
    skipped_count: int
