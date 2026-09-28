"""API params."""

from typing import Self

from fastapi import Query
from pydantic import BaseModel, Field, model_validator

from tanbun.feature.entry.domain import (
    ResourceOrderKey,
    StatsOrderKey,
    UserOrderKey,
)
from tanbun.feature.entry.resource.repo.diff_update.errors import IdentityKind
from tanbun.feature.repo.cypher import Paging


class IdentityResolutionBody(BaseModel, frozen=True):
    """1件の同一性競合に対するユーザーの選択."""

    kind: IdentityKind = "sentence"
    original: str
    replacement: str | None


class ResourceTextBody(BaseModel, frozen=True):
    """テキストResourceの保存と任意の競合解決."""

    txt: str
    path: list[str]
    identity_resolutions: list[IdentityResolutionBody] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_originals(self) -> Self:
        """同じ旧文に複数の選択を送る曖昧なリクエストを拒否する."""
        keys = [
            (resolution.kind, resolution.original)
            for resolution in self.identity_resolutions
        ]
        if len(keys) != len(set(keys)):
            msg = "同じ旧値の解決指定が重複しています"
            raise ValueError(msg)
        return self

    def resolution_map(self, kind: IdentityKind) -> dict[str, str | None]:
        """指定種別だけをdomain用の辞書へ変換する."""
        return {
            resolution.original: resolution.replacement
            for resolution in self.identity_resolutions
            if resolution.kind == kind
        }


class ResourceSearchBody(BaseModel, frozen=True):
    """リソース検索のPOST Body."""

    q: str = ""
    q_user: str = ""
    paging: Paging = Field(default_factory=Paging)
    desc: bool = True
    order_by: list[ResourceOrderKey | StatsOrderKey | UserOrderKey] | None = None


class ResourceSearchParams(BaseModel, frozen=True):
    """検索方法の指定."""

    q: str = Query("", title="title検索文字列")
    q_user: str = Query(
        "",
        title="user名検索文字列",
        description="username or display_nameでマッチするリソースを検索する",
    )
    paging: Paging = Field(default_factory=Paging)
    desc: bool = Query(default=True, description="降順")


def get_search_resource_param(
    q: str = Query("", description="title検索文字列"),
    q_user: str = Query(
        "",
        title="user名検索文字列",
        description="username or display_nameでマッチするリソースを検索する",
    ),
    page: int = Query(default=1, gt=0),
    size: int = Query(default=100, gt=0),
    desc: bool = Query(default=True, description="降順"),  # noqa: FBT001
) -> ResourceSearchParams:
    """検索方法の指定."""
    paging = Paging(page=page, size=size)
    return ResourceSearchParams(q=q, q_user=q_user, paging=paging, desc=desc)
