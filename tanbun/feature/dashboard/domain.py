"""個人ダッシュボードの表示モデル."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from tanbun.feature.domain.datetime import Neo4jDateTime


class PersonalTanbunItem(BaseModel, frozen=True):
    """個人TLへ表示する単文."""

    uid: UUID
    sentence: str
    resource_uid: UUID
    resource_name: str
    updated_at: Neo4jDateTime | None
    exposure_count: int
    seen_today: bool


class TanbunExposureResult(BaseModel, frozen=True):
    """1日1回の単文閲覧記録結果."""

    sentence_id: UUID
    seen_on: date
    exposure_count: int
    recorded: bool
