"""単文routerのparameter."""

from typing import Literal
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, Field

from tanbun.feature.repo.cypher import Paging
from tanbun.feature.tanbun.repo.clause import OrderBy, WherePhrase


class SearchParam(BaseModel):
    """検索パラメータ."""

    q: str = Query("")
    type: str = Query(WherePhrase.CONTAINS.name, enum=[p.name for p in WherePhrase])
    paging: Paging = Field(default_factory=Paging)
    order: OrderBy | None = None
    sort: Literal["score", "pagerank"] = "score"
    resource_id: UUID | None = None


def get_search_param(  # noqa: PLR0917
    q: str = Query(""),
    type: str = Query(WherePhrase.CONTAINS.name, enum=[p.name for p in WherePhrase]),  # noqa: A002
    page: int = Query(default=1, gt=0),
    size: int = Query(default=100, gt=0),
    n_detail: int = Query(default=1),
    n_premise: int = Query(default=3),
    n_conclusion: int = Query(default=3),
    n_refer: int = Query(default=3),
    n_referred: int = Query(default=3),
    desc: bool = Query(default=True),  # noqa: FBT001
    sort: Literal["score", "pagerank"] = Query(default="score"),
    resource_id: UUID | None = Query(default=None),
) -> SearchParam:
    """orderByがうまく変換されなかったので噛ませた."""
    paging = Paging(page=page, size=size)
    order = OrderBy(
        n_detail=n_detail,
        n_premise=n_premise,
        n_conclusion=n_conclusion,
        n_refer=n_refer,
        n_referred=n_referred,
        desc=desc,
    )
    return SearchParam(
        q=q,
        type=type,
        paging=paging,
        order=order,
        sort=sort,
        resource_id=resource_id,
    )
