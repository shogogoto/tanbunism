"""ユーザー通知のドメインモデル."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tanbun.feature.domain.datetime import Neo4jDateTime


class NotificationKind(StrEnum):
    """通知の種類."""

    QUIZ_PREPARATION_COMPLETE = "quiz_preparation_complete"
    QUIZ_PREPARATION_FAILED = "quiz_preparation_failed"
    QUIZ_ISSUE_REPORTED = "quiz_issue_reported"


class NewNotification(BaseModel, frozen=True):
    """作成する通知."""

    kind: NotificationKind
    title: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    href: str | None = Field(default=None, max_length=500)


class Notification(NewNotification, frozen=True):
    """保存された通知."""

    uid: UUID
    created: Neo4jDateTime
    read_at: Neo4jDateTime | None


class NotificationFeed(BaseModel, frozen=True):
    """通知一覧と全体の未読数."""

    notifications: list[Notification]
    unread_count: int


class MarkAllNotificationsReadResult(BaseModel, frozen=True):
    """一括既読の結果."""

    updated_count: int


class PushSubscriptionKeys(BaseModel, frozen=True):
    """Push APIが発行した暗号鍵."""

    p256dh: str = Field(min_length=1, max_length=512)
    auth: str = Field(min_length=1, max_length=512)


class PushSubscriptionDraft(BaseModel, frozen=True):
    """ブラウザから受け取るWeb Push購読情報."""

    model_config = ConfigDict(populate_by_name=True)

    endpoint: str = Field(min_length=1, max_length=4096)
    expiration_time: float | None = Field(default=None, alias="expirationTime")
    keys: PushSubscriptionKeys

    @field_validator("endpoint")
    @classmethod
    def require_secure_endpoint(cls, value: str) -> str:
        """Pushサービスへの送信先はHTTPSだけを受け入れる."""
        if not value.startswith("https://"):
            msg = "Push subscription endpoint must use HTTPS"
            raise ValueError(msg)
        return value


class PushSubscriptionEndpoint(BaseModel, frozen=True):
    """解除するWeb Push購読の識別情報."""

    endpoint: str = Field(min_length=1, max_length=4096)

    @field_validator("endpoint")
    @classmethod
    def require_secure_endpoint(cls, value: str) -> str:
        """Pushサービスへの送信先はHTTPSだけを受け入れる."""
        if not value.startswith("https://"):
            msg = "Push subscription endpoint must use HTTPS"
            raise ValueError(msg)
        return value


class PushConfiguration(BaseModel, frozen=True):
    """ブラウザが購読に使う公開設定."""

    enabled: bool
    public_key: str | None
