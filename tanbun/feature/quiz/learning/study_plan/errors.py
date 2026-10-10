"""StudyPlanのエラー."""

from fastapi import status

from tanbun.feature.domain.errors import DomainError


class StudyPlanCreateError(DomainError):
    """StudyPlanを作成できなかった."""

    status_code = status.HTTP_400_BAD_REQUEST


class StudyPlanNotFoundError(DomainError):
    """所有するStudyPlanが見つからなかった."""

    status_code = status.HTTP_404_NOT_FOUND


class DefaultStudyPlanDeletionError(DomainError):
    """ゲーム連携された既定StudyPlanは削除できない."""

    status_code = status.HTTP_409_CONFLICT


class StudyPlanResourceAccessError(DomainError):
    """StudyPlanへ登録できないresourceが指定された."""

    status_code = status.HTTP_403_FORBIDDEN
