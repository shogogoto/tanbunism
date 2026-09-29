"""管理者向けメンテナンスAPIのモデル."""

from enum import StrEnum, auto
from uuid import UUID

from pydantic import BaseModel, Field

from tanbun.feature.domain.datetime import Neo4jDateTime


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


class AdminUserItem(BaseModel, frozen=True):
    """管理画面に表示するユーザー."""

    uid: UUID
    email: str
    display_name: str | None
    username: str | None
    is_active: bool
    is_superuser: bool
    created: Neo4jDateTime
    resource_count: int


class UpdateUserStatusRequest(BaseModel, frozen=True):
    """ユーザーの利用可否変更."""

    is_active: bool


class AdminResourceItem(BaseModel, frozen=True):
    """管理対象ユーザーが所有するResource."""

    uid: UUID
    name: str
    updated_at: Neo4jDateTime | None
    sentence_count: int


class ResourceDeletionImpact(BaseModel, frozen=True):
    """Resource削除で影響を受けるデータ数."""

    resource_uid: UUID
    resource_name: str
    owner_uid: UUID
    owner_email: str
    sentence_count: int
    term_count: int
    quiz_count: int
    answer_count: int
    retiring_sentence_count: int
    deleting_sentence_count: int


class DeleteAdminResourceRequest(BaseModel, frozen=True):
    """Resource名による削除確認."""

    confirmation: str = Field(min_length=1)


class DeleteAdminResourceResult(BaseModel, frozen=True):
    """管理者によるResource削除結果."""

    resource_uid: UUID
    deleted_sentence_count: int
    retired_sentence_count: int
