"""StudyPlan API."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Response, status

from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.study_plan.domain import (
    PrepareStudyPlanRequest,
    PrepareStudyPlanResult,
    PrepareStudyPlansAccepted,
    PrepareStudyPlansRequest,
    StudyPlan,
    StudyPlanDraft,
    StudyPlanPreparationStatus,
)
from tanbun.feature.quiz.learning.study_plan.schema import (
    QuizRecommendationResponse,
)
from tanbun.feature.quiz.learning.study_plan.usecase import (
    create_study_plan,
    delete_study_plan,
    get_study_plan,
    get_study_plan_preparation_status,
    get_study_plan_preparation_statuses,
    get_study_plans,
    prepare_additional_study_plan_quizzes,
    prepare_study_plans_in_background,
    recommend_quizzes_for_study_plan,
    update_study_plan,
    validate_study_plans_for_preparation,
)
from tanbun.feature.user.router_util import ActiveUser

_router = APIRouter(prefix="/study-plans", tags=["quiz-learning"])


@_router.post("", status_code=status.HTTP_201_CREATED)
async def create_study_plan_api(
    draft: StudyPlanDraft,
    user: ActiveUser,
) -> StudyPlan:
    """StudyPlanを作成."""
    return await create_study_plan(user.uid, draft)


@_router.get("")
async def list_study_plans_api(user: ActiveUser) -> list[StudyPlan]:
    """所有するStudyPlanを一覧取得."""
    return await get_study_plans(user.uid)


@_router.get("/preparations")
async def list_study_plan_preparations_api(
    user: ActiveUser,
) -> list[StudyPlanPreparationStatus]:
    """所有するStudyPlanの準備済み問題数を一括取得."""
    return await get_study_plan_preparation_statuses(user.uid)


@_router.post("/prepare", status_code=status.HTTP_202_ACCEPTED)
async def prepare_study_plans_api(
    body: PrepareStudyPlansRequest,
    background_tasks: BackgroundTasks,
    user: ActiveUser,
) -> PrepareStudyPlansAccepted:
    """選択したStudyPlanのクイズ準備をバックグラウンドで開始する."""
    await validate_study_plans_for_preparation(body.plan_ids, user.uid)
    background_tasks.add_task(
        prepare_study_plans_in_background,
        body.plan_ids,
        user.uid,
        body.additional_count,
    )
    return PrepareStudyPlansAccepted(accepted_count=len(body.plan_ids))


@_router.get("/{plan_id}")
async def get_study_plan_api(
    plan_id: UUID,
    user: ActiveUser,
) -> StudyPlan:
    """所有するStudyPlanを取得."""
    return await get_study_plan(plan_id, user.uid)


@_router.put("/{plan_id}")
async def update_study_plan_api(
    plan_id: UUID,
    draft: StudyPlanDraft,
    user: ActiveUser,
) -> StudyPlan:
    """StudyPlan全体を更新."""
    return await update_study_plan(plan_id, user.uid, draft)


@_router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_study_plan_api(
    plan_id: UUID,
    user: ActiveUser,
) -> Response:
    """StudyPlanを削除."""
    await delete_study_plan(plan_id, user.uid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@_router.get("/{plan_id}/preparation")
async def get_study_plan_preparation_api(
    plan_id: UUID,
    user: ActiveUser,
) -> StudyPlanPreparationStatus:
    """StudyPlanの準備済み問題数を取得."""
    return await get_study_plan_preparation_status(plan_id, user.uid)


@_router.post("/{plan_id}/prepare")
async def prepare_study_plan_api(
    plan_id: UUID,
    body: PrepareStudyPlanRequest,
    user: ActiveUser,
) -> PrepareStudyPlanResult:
    """StudyPlanへ指定数の新しい問題を追加."""
    return await prepare_additional_study_plan_quizzes(
        plan_id,
        user.uid,
        body.additional_count,
    )


@_router.post("/{plan_id}/recommendations")
async def recommend_study_plan_quizzes_api(
    plan_id: UUID,
    user: ActiveUser,
    quiz_type: QuizType | None = None,
    *,
    generate_missing: bool = True,
) -> list[QuizRecommendationResponse]:
    """StudyPlanの設定で回答可能なクイズを推薦."""
    recommendations = await recommend_quizzes_for_study_plan(
        plan_id,
        user.uid,
        quiz_type,
        generate_missing=generate_missing,
    )
    return [
        QuizRecommendationResponse.from_domain(recommendation)
        for recommendation in recommendations
    ]


def study_plan_router() -> APIRouter:
    """StudyPlan router."""
    return _router
