"""個人ダッシュボードの表示モデル."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from tanbun.feature.domain.datetime import Neo4jDateTime


class PersonalTanbunItem(BaseModel, frozen=True):
    """個人TLへ表示する単文."""

    uid: UUID
    sentence: str
    term_names: list[str]
    resource_uid: UUID
    resource_name: str
    updated_at: Neo4jDateTime | None
    score: int
    exposure_count: int
    seen_today: bool
    seen_in_set: bool = False


class TanbunExposureResult(BaseModel, frozen=True):
    """1日1回の単文閲覧記録結果."""

    sentence_id: UUID
    seen_on: date
    exposure_count: int
    recorded: bool


class TodayTanbunExposureCount(BaseModel, frozen=True):
    """今日「見たよ」を記録した単文数."""

    seen_on: date
    count: int
