"""StudyPlanのユースケース."""

import logging

from tanbun.feature.domain.types import UUIDy
from tanbun.feature.entry.resource.repo.owner import check_entry_owner
from tanbun.feature.notification.domain import NewNotification, NotificationKind
from tanbun.feature.notification.usecase import notify_user
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.learning.fill.usecase import generate_quizzes
from tanbun.feature.quiz.learning.recommendation.domain import (
    QuizRecommendation,
)
from tanbun.feature.quiz.learning.recommendation.usecase import (
    recommend_quizzes,
)
from tanbun.feature.quiz.learning.selection.domain import QuizFillStrategy
from tanbun.feature.quiz.learning.study_plan.domain import (
    PrepareStudyPlanResult,
    StudyPlan,
    StudyPlanDraft,
    StudyPlanPreparationStatus,
)
from tanbun.feature.quiz.learning.study_plan.errors import (
    StudyPlanNotFoundError,
    StudyPlanResourceAccessError,
)
from tanbun.feature.quiz.learning.study_plan.repo import (
    count_prepared_quizzes,
    fetch_study_plan,
    list_prepared_quiz_counts,
    list_study_plans,
)
from tanbun.feature.quiz.learning.study_plan.repo import (
    create_study_plan as create_study_plan_in_repo,
)
from tanbun.feature.quiz.learning.study_plan.repo import (
    delete_study_plan as delete_study_plan_in_repo,
)
from tanbun.feature.quiz.learning.study_plan.repo import (
    update_study_plan as update_study_plan_in_repo,
)

logger = logging.getLogger(__name__)
MAX_FAILURE_DETAILS = 3


async def _check_resource_ownership(
    user_id: UUIDy,
    draft: StudyPlanDraft,
) -> None:
    """StudyPlanの全resourceをユーザーが所有しているか確認."""
    for resource_id in draft.resource_ids:
        if not await check_entry_owner(user_id, resource_id):
            msg = f"所有していないresourceはStudyPlanへ登録できません: {resource_id}"
            raise StudyPlanResourceAccessError(msg=msg)


async def create_study_plan(
    user_id: UUIDy,
    draft: StudyPlanDraft,
) -> StudyPlan:
    """所有resourceからStudyPlanを作成."""
    await _check_resource_ownership(user_id, draft)
    return await create_study_plan_in_repo(user_id, draft)


async def get_study_plan(
    plan_id: UUIDy,
    user_id: UUIDy,
) -> StudyPlan:
    """所有するStudyPlanを取得."""
    plan = await fetch_study_plan(plan_id, user_id)
    if plan is None:
        msg = f"StudyPlanが見つかりません: {plan_id}"
        raise StudyPlanNotFoundError(msg=msg)
    return plan


async def get_study_plans(user_id: UUIDy) -> list[StudyPlan]:
    """所有するStudyPlanを一覧取得."""
    return await list_study_plans(user_id)


async def update_study_plan(
    plan_id: UUIDy,
    user_id: UUIDy,
    draft: StudyPlanDraft,
) -> StudyPlan:
    """所有するStudyPlanを更新."""
    await _check_resource_ownership(user_id, draft)
    plan = await update_study_plan_in_repo(plan_id, user_id, draft)
    if plan is None:
        msg = f"StudyPlanが見つかりません: {plan_id}"
        raise StudyPlanNotFoundError(msg=msg)
    return plan


async def delete_study_plan(
    plan_id: UUIDy,
    user_id: UUIDy,
) -> None:
    """所有するStudyPlanを削除."""
    if not await delete_study_plan_in_repo(plan_id, user_id):
        msg = f"StudyPlanが見つかりません: {plan_id}"
        raise StudyPlanNotFoundError(msg=msg)


async def get_study_plan_preparation_status(
    plan_id: UUIDy,
    user_id: UUIDy,
) -> StudyPlanPreparationStatus:
    """StudyPlanで現在回答可能な準備済みクイズ数を返す."""
    plan = await get_study_plan(plan_id, user_id)
    return StudyPlanPreparationStatus(
        plan_id=plan.uid,
        prepared_quiz_count=await count_prepared_quizzes(plan.uid, user_id),
    )


async def get_study_plan_preparation_statuses(
    user_id: UUIDy,
) -> list[StudyPlanPreparationStatus]:
    """所有する全StudyPlanの準備済みクイズ数を返す."""
    counts = await list_prepared_quiz_counts(user_id)
    return [
        StudyPlanPreparationStatus(
            plan_id=plan_id,
            prepared_quiz_count=count,
        )
        for plan_id, count in counts.items()
    ]


def _allocate_counts(total: int, size: int) -> list[int]:
    """入力順を維持して追加数を均等配分する."""
    quotient, remainder = divmod(total, size)
    return [quotient + (index < remainder) for index in range(size)]


async def prepare_additional_study_plan_quizzes(
    plan_id: UUIDy,
    user_id: UUIDy,
    additional_count: int,
    *,
    send_notification: bool = True,
) -> PrepareStudyPlanResult:
    """Planの形式・Resourceへ未coverageのクイズを追加する."""
    plan = await get_study_plan(plan_id, user_id)
    before = await count_prepared_quizzes(plan.uid, user_id)
    targets = [
        (quiz_type, resource_id)
        for quiz_type in plan.quiz_types
        for resource_id in plan.resource_ids
    ]
    counts = _allocate_counts(additional_count, len(targets))
    generated_count = 0
    for (quiz_type, resource_id), count in zip(targets, counts, strict=True):
        if count == 0:
            continue
        quizzes = await generate_quizzes(
            resource_id,
            user_id,
            quiz_type,
            QuizFillStrategy.COVERAGE,
            CandidateType.ALL,
            n_quiz=count,
            n_option=plan.n_option,
        )
        generated_count += len(quizzes)

    # ある形式で候補が足りなくても、生成可能な別形式で指定数まで補う。
    remaining = additional_count - generated_count
    if remaining > 0:
        for quiz_type, resource_id in targets:
            quizzes = await generate_quizzes(
                resource_id,
                user_id,
                quiz_type,
                QuizFillStrategy.COVERAGE,
                CandidateType.ALL,
                n_quiz=remaining,
                n_option=plan.n_option,
            )
            remaining -= len(quizzes)
            if remaining == 0:
                break
    prepared = await count_prepared_quizzes(plan.uid, user_id)
    added = max(0, prepared - before)
    if added > 0 and send_notification:
        await notify_user(
            user_id,
            NewNotification(
                kind=NotificationKind.QUIZ_PREPARATION_COMPLETE,
                title="クイズの準備完了",
                description=(
                    f"「{plan.name}」に{added}問追加しました。"
                    f"準備済みは合計{prepared}問です。"
                ),
                href="/dashboard?view=study-plans",
            ),
        )
    return PrepareStudyPlanResult(
        plan_id=plan.uid,
        requested_count=additional_count,
        added_count=added,
        prepared_quiz_count=prepared,
    )


async def validate_study_plans_for_preparation(
    plan_ids: list[UUIDy],
    user_id: UUIDy,
) -> None:
    """バックグラウンドへ渡す前に全Planの所有権を確認する."""
    for plan_id in plan_ids:
        await get_study_plan(plan_id, user_id)


async def prepare_study_plans_in_background(
    plan_ids: list[UUIDy],
    user_id: UUIDy,
    additional_count: int,
) -> None:
    """複数Planを順番に準備し、一つの完了通知へ集約する."""
    completed = 0
    failed = 0
    added = 0
    failure_details: list[str] = []
    try:
        for plan_id in plan_ids:
            plan_name = str(plan_id)
            try:
                plan = await get_study_plan(plan_id, user_id)
                plan_name = plan.name
                result = await prepare_additional_study_plan_quizzes(
                    plan.uid,
                    user_id,
                    additional_count,
                    send_notification=False,
                )
                completed += 1
                added += result.added_count
            except Exception as error:
                failed += 1
                if len(failure_details) < MAX_FAILURE_DETAILS:
                    reason = str(error).strip() or type(error).__name__
                    failure_details.append(f"「{plan_name}」: {reason}")
                logger.exception("StudyPlan quiz preparation failed: %s", plan_id)

        if completed and added > 0:
            title = (
                "クイズの一括準備完了" if failed == 0 else "クイズの一括準備が一部完了"
            )
            description = f"{completed}件の学習計画を処理し、{added}問追加しました。"
            if failed:
                description += f" {failed}件は準備できませんでした。"
            kind = NotificationKind.QUIZ_PREPARATION_COMPLETE
        elif completed:
            title = "追加できるクイズがありませんでした"
            description = (
                f"{completed}件の学習計画を確認しましたが、"
                "未出題の候補または選択肢が不足しています。"
            )
            if failed:
                description += f" {failed}件は処理に失敗しました。"
            kind = NotificationKind.QUIZ_PREPARATION_FAILED
        else:
            title = "クイズを準備できませんでした"
            description = f"選択した{failed}件の学習計画を確認してください。"
            kind = NotificationKind.QUIZ_PREPARATION_FAILED

        if failure_details:
            description += " 原因: " + " / ".join(failure_details)
            description = description[:500]

        await notify_user(
            user_id,
            NewNotification(
                kind=kind,
                title=title,
                description=description,
                href="/dashboard?view=study-plans",
            ),
        )
    except Exception:
        logger.exception("Failed to finish StudyPlan preparation notification")


async def recommend_quizzes_for_study_plan(
    plan_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType | None = None,
    *,
    generate_missing: bool = True,
    limit: int | None = None,
) -> list[QuizRecommendation]:
    """保存されたStudyPlanの設定でクイズを推薦."""
    plan = await get_study_plan(plan_id, user_id)

    # 過去に保存された少ない出題数でも、選択した各形式を最低1問は試す。
    n_quiz = max(limit if limit is not None else plan.n_quiz, len(plan.quiz_types))
    quotient, remainder = divmod(n_quiz, len(plan.quiz_types))
    quiz_types = (
        [quiz_type]
        if quiz_type is not None and quiz_type in plan.quiz_types
        else plan.quiz_types
        if quiz_type is None
        else []
    )
    pools = []
    for current_quiz_type in quiz_types:
        index = plan.quiz_types.index(current_quiz_type)
        count = quotient + (index < remainder)
        pools.append(
            await recommend_quizzes(
                plan.resource_ids,
                user_id,
                current_quiz_type,
                CandidateType.ALL,
                count,
                plan.n_option,
                generate_missing=generate_missing,
            ),
        )

    longest = max((len(pool) for pool in pools), default=0)
    return [
        pool[index] for index in range(longest) for pool in pools if index < len(pool)
    ]
