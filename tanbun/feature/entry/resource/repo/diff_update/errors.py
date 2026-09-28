"""差分更新の同一性判定エラー."""

from dataclasses import dataclass
from typing import Any, Literal

from fastapi import status
from pydantic import BaseModel

from tanbun.feature.domain.errors import DomainError


@dataclass(frozen=True)
class IdentityCandidate:
    """同一とみなせる更新先候補."""

    value: str
    similarity: float


@dataclass(frozen=True)
class IdentityConflict:
    """ユーザーによる解決が必要な同一性競合."""

    original: str
    candidates: tuple[IdentityCandidate, ...]


type IdentityKind = Literal["sentence", "term"]


class IdentityCandidateResponse(BaseModel, frozen=True):
    """競合画面へ返す更新候補."""

    value: str
    similarity: float


class IdentityConflictItemResponse(BaseModel, frozen=True):
    """競合画面へ返す旧値と候補群."""

    original: str
    candidates: list[IdentityCandidateResponse]


class IdentityConflictResponse(BaseModel, frozen=True):
    """HTTP 409の機械可読な同一性競合."""

    code: int
    message: str
    type: Literal["identity_conflict"] = "identity_conflict"
    kind: IdentityKind
    conflicts: list[IdentityConflictItemResponse]


class IdentificationError(DomainError):
    """同定失敗."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(
        self,
        conflicts: tuple[IdentityConflict, ...],
        kind: IdentityKind = "sentence",
    ) -> None:
        """競合の機械可読な詳細を保持する."""
        self.conflicts = conflicts
        self.kind = kind
        originals = "、".join(f"'{conflict.original}'" for conflict in conflicts)
        super().__init__(f"更新先を一意に決められません: {originals}")

    @property
    def detail(self) -> dict[str, Any]:
        """Git風の競合解決UIで利用できるJSONを返す."""
        return IdentityConflictResponse(
            **super().detail,
            kind=self.kind,
            conflicts=[
                {
                    "original": conflict.original,
                    "candidates": [
                        {
                            "value": candidate.value,
                            "similarity": candidate.similarity,
                        }
                        for candidate in conflict.candidates
                    ],
                }
                for conflict in self.conflicts
            ],
        ).model_dump()


class InvalidIdentityResolutionError(DomainError):
    """クライアントが送った同一性解決が現在の差分と整合しない."""

    def __init__(self, msg: str) -> None:
        """不正な解決内容を説明する."""
        super().__init__(msg)

    @property
    def detail(self) -> dict[str, Any]:
        """競合解決UIが判別できるJSONを返す."""
        return {
            **super().detail,
            "type": "invalid_identity_resolution",
        }
