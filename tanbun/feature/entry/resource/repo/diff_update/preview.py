"""Resource差分の保存前プレビュー."""

from uuid import UUID

from pydantic import BaseModel

from tanbun.feature.entry.resource.repo.diff_update.repo import ResourceDiffPlan
from tanbun.feature.parsing.sysnet import SysNet


class ResourceDiffPreview(BaseModel, frozen=True):
    """Webの競合確認画面へ返す、DB非更新の差分概要."""

    resource_id: UUID | None
    is_new: bool
    sentences_added: int
    sentences_removed: int
    sentences_updated: int
    terms_added: int
    terms_removed: int
    terms_updated: int

    @classmethod
    def for_new(cls, network: SysNet) -> "ResourceDiffPreview":
        """新規Resourceの概要を作る."""
        return cls(
            resource_id=None,
            is_new=True,
            sentences_added=len(network.sentences),
            sentences_removed=0,
            sentences_updated=0,
            terms_added=len(network.terms),
            terms_removed=0,
            terms_updated=0,
        )

    @classmethod
    def from_plan(
        cls,
        resource_id: UUID,
        plan: ResourceDiffPlan,
    ) -> "ResourceDiffPreview":
        """検証済み差分計画から概要を作る."""
        return cls(
            resource_id=resource_id,
            is_new=False,
            sentences_added=len(plan.added_sentences),
            sentences_removed=len(plan.removed_sentences),
            sentences_updated=len(plan.updated_sentences),
            terms_added=len(plan.added_terms),
            terms_removed=len(plan.removed_terms),
            terms_updated=len(plan.updated_terms),
        )
