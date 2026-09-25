"""複数リソースを横断するクイズ推薦ユースケース."""

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.domain import QuizSource
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.fill.usecase import generate_quizzes
from tanbun.feature.quiz.learning.recommendation.domain import (
    QuizRecommendation,
    QuizRecommendationReason,
)
from tanbun.feature.quiz.learning.review.domain import ReviewQuizKind
from tanbun.feature.quiz.learning.review.usecase import (
    prepare_review_quizzes_with_reason,
)
from tanbun.feature.quiz.learning.selection.domain import QuizFillStrategy


def _allocate_counts(n_quiz: int, n_resource: int) -> list[int]:
    """入力順を優先してクイズ件数を均等配分."""
    quotient, remainder = divmod(n_quiz, n_resource)
    return [quotient + (index < remainder) for index in range(n_resource)]


def _round_robin(
    pools: list[list[QuizRecommendation]],
) -> list[QuizRecommendation]:
    """リソースごとの推薦を一件ずつ交互に並べる."""
    longest = max((len(pool) for pool in pools), default=0)
    return [
        pool[index] for index in range(longest) for pool in pools if index < len(pool)
    ]


async def _prepare_resource_quizzes(  # noqa: PLR0917
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
    candidate_type: CandidateType,
    n_quiz: int,
    n_option: int,
    *,
    generate_missing: bool,
) -> list[tuple[QuizSource, QuizRecommendationReason]]:
    """復習対象を優先し、不足分を未coverage対象から生成."""
    reviews = await prepare_review_quizzes_with_reason(
        resource_id,
        user_id,
        quiz_type,
        candidate_type,
        n_quiz=n_quiz,
        n_option=n_option,
        generate_missing=generate_missing,
    )
    prepared_reviews = [
        (
            item.quiz,
            QuizRecommendationReason.UNATTEMPTED
            if item.kind is ReviewQuizKind.UNATTEMPTED
            else QuizRecommendationReason.LOW_ACCURACY,
        )
        for item in reviews
    ]
    n_missing = n_quiz - len(reviews)
    if n_missing == 0 or not generate_missing:
        return prepared_reviews

    new_quizzes = await generate_quizzes(
        resource_id,
        user_id,
        quiz_type,
        QuizFillStrategy.COVERAGE,
        candidate_type,
        n_quiz=n_missing,
        n_option=n_option,
        exclude_target_ids=[item.quiz.target_id for item in reviews],
    )
    return [
        *prepared_reviews,
        *[(quiz, QuizRecommendationReason.COVERAGE) for quiz in new_quizzes],
    ]


async def recommend_quizzes(  # noqa: PLR0917
    resource_ids: list[UUIDy],
    user_id: UUIDy,
    quiz_type: QuizType,
    candidate_type: CandidateType,
    n_quiz: int,
    n_option: int,
    *,
    generate_missing: bool = True,
) -> list[QuizRecommendation]:
    """指定リソースから学習・復習クイズをラウンドロビン推薦."""
    if n_quiz < 0:
        msg = "n_quizは0以上を指定してください"
        raise ValueError(msg)

    resources = list(dict.fromkeys(to_uuid(uid) for uid in resource_ids))
    if not resources or n_quiz == 0:
        return []

    counts = _allocate_counts(n_quiz, len(resources))
    pools = []
    for resource_id, count in zip(resources, counts, strict=True):
        if count == 0:
            continue
        quizzes = await _prepare_resource_quizzes(
            resource_id,
            user_id,
            quiz_type,
            candidate_type,
            count,
            n_option,
            generate_missing=generate_missing,
        )
        pools.append(
            [
                QuizRecommendation(
                    resource_id=resource_id,
                    quiz=quiz,
                    reason=reason,
                )
                for quiz, reason in quizzes
            ],
        )
    return _round_robin(pools)
