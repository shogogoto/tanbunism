"""domain."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, RootModel

from tanbun.feature.domain.datetime import TZ, Neo4jDateTime
from tanbun.feature.entry.domain import NameSpace
from tanbun.feature.user.public_schema import UserReadPublic

UserSearchOrderKey = Literal[
    "username",
    "display_name",
    "n_char",
    "n_sentence",
    "n_resource",
]


class UserAchievement(BaseModel, frozen=True):
    """ユーザーの作業量計."""

    n_char: int = Field(title="文字数")
    n_sentence: int = Field(title="単文数")
    n_resource: int = Field(title="リソース数")
    created: Neo4jDateTime

    @classmethod
    def from_namspace(cls, ns: NameSpace) -> Self:
        """名前空間のリソース情報から合算."""
        n_char = 0
        n_sentence = 0
        for v in ns.stats.values():
            n_char += v.n_char
            n_sentence += v.n_sentence
        return cls(
            n_char=n_char,
            n_sentence=n_sentence,
            n_resource=len(ns.stats.values()),
            created=datetime.now(tz=TZ),
        )


class UserSearchRow(BaseModel, frozen=True):
    """検索結果行."""

    user: UserReadPublic
    archivement: UserAchievement
    level: int


class UserSearchResult(BaseModel, frozen=True):
    """検索結果."""

    total: int
    data: list[UserSearchRow]


class AchievementHistory(BaseModel, frozen=True):
    """成果履歴."""

    user: UserReadPublic
    archivements: list[UserAchievement]


class AchievementHistories(RootModel[list[AchievementHistory]]):
    """成果履歴."""


class UserActivity(BaseModel, frozen=True):
    """ユーザーの活動状況."""

    user: UserReadPublic
    latest: UserAchievement | None
    current: UserAchievement


class UserActivities(RootModel[list[UserActivity]]):
    """ユーザーの活動状況."""
