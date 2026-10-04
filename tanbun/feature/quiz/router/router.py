"""quiz router."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from tanbun.feature.entry.errors import NotOwnerError
from tanbun.feature.entry.resource.repo.owner import check_entry_owner
from tanbun.feature.notification.domain import NewNotification, NotificationKind
from tanbun.feature.notification.usecase import notify_user
from tanbun.feature.quiz.answering.usecase import answer_quiz_as_chain
from tanbun.feature.quiz.chain.domain import QuizChain
from tanbun.feature.quiz.chain.router import quiz_chain_router
from tanbun.feature.quiz.domain.answer import AnswerHistoryResult, Answers
from tanbun.feature.quiz.domain.collections import ReadableQuizResult
from tanbun.feature.quiz.domain.domain import ReadableQuiz
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.quiz.learning.progress.domain import ResourceLearningStatus
from tanbun.feature.quiz.learning.progress.usecase import fetch_learning_status
from tanbun.feature.quiz.learning.study_plan.router import study_plan_router
from tanbun.feature.quiz.listing.repo import (
    list_answer_history,
    list_answers,
    list_learning_quizzes,
    list_quiz_by_user_ids,
    list_quiz_feed,
    search_created_quizzes,
)
from tanbun.feature.quiz.management.domain import (
    BrokenQuizReference,
    DeleteQuizzesResult,
    ManagedQuizResult,
    QuizReattachmentResult,
    QuizReport,
    QuizReportRequest,
    QuizResourceStatus,
    SentenceQuizStatus,
    UnplannedQuiz,
)
from tanbun.feature.quiz.management.repo import (
    list_broken_created_quiz_references,
    list_created_quiz_resource_statuses,
    list_created_quiz_sentence_statuses,
    list_reported_created_quizzes,
    list_unplanned_created_quizzes,
    report_quiz_issue,
)
from tanbun.feature.quiz.management.usecase import (
    delete_quiz,
    delete_quizzes,
    repair_quiz_reference,
)
from tanbun.feature.quiz.router.params import (
    AnswerParam,
    CreateQuizParam,
    DeleteQuizzesParam,
    RepairQuizReferenceParam,
)
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.user.router_util import ActiveUser

_r = APIRouter(prefix="/quiz", tags=["quiz"])


@_r.post("")
async def create_quiz_api(
    param: CreateQuizParam,
    user: ActiveUser,
) -> ReadableQuiz:
    """単文指定してクイズを作成.

    単文を追っている途中で思いつきでクイズを作る
    """
    src = await generate_quiz(
        param.quiz_type,
        param.cand_type,
        param.target_sent_uid,
        param.n_option,
        user_id=user.uid,
        correct_sent_uids=param.correct_sent_uids,
    )
    return src.to_readable()


@_r.get("")
async def list_quiz(
    user: ActiveUser,
    page: Annotated[int, Query(gt=0)] = 1,
    size: Annotated[int, Query(gt=0)] = 100,
) -> ReadableQuizResult:
    """認証ユーザーに用意された学習対象クイズを一覧取得."""
    return await list_learning_quizzes(
        user.uid,
        Paging(page=page, size=size),
    )


@_r.get("/created")
async def list_created_quizzes(
    user: ActiveUser,
    resource_id: UUID | None = None,
    sentence_id: UUID | None = None,
    page: Annotated[int, Query(gt=0)] = 1,
    size: Annotated[int, Query(gt=0)] = 100,
) -> ReadableQuizResult:
    """認証ユーザー自身が作成したクイズを一覧取得."""
    return await list_quiz_by_user_ids(
        [user.uid],
        Paging(page=page, size=size),
        resource_ids=[resource_id] if resource_id is not None else None,
        sentence_ids=[sentence_id] if sentence_id is not None else None,
    )


@_r.get("/feed")
async def list_quiz_feed_api(
    user: ActiveUser,
    page: Annotated[int, Query(gt=0)] = 1,
    size: Annotated[int, Query(gt=0, le=100)] = 20,
) -> ManagedQuizResult:
    """全ユーザーが作成したQuizを閲覧者の回答状況付きで取得."""
    return await list_quiz_feed(user.uid, Paging(page=page, size=size))


@_r.get("/created/resources")
async def list_created_quiz_resources(
    user: ActiveUser,
) -> list[QuizResourceStatus]:
    """作成済みQuizの状況をResourceごとに取得."""
    return await list_created_quiz_resource_statuses(user.uid)


@_r.get("/created/search")
async def search_created_quizzes_api(
    user: ActiveUser,
    *,
    q: Annotated[str | None, Query(max_length=200)] = None,
    quiz_types: Annotated[list[QuizType] | None, Query()] = None,
    answered: bool | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    min_accuracy: Annotated[float | None, Query(ge=0, le=1)] = None,
    max_accuracy: Annotated[float | None, Query(ge=0, le=1)] = None,
    resource_id: UUID | None = None,
    sentence_id: UUID | None = None,
    page: Annotated[int, Query(gt=0)] = 1,
    size: Annotated[int, Query(gt=0)] = 100,
) -> ManagedQuizResult:
    """作成Quizを文字列・形式・回答状態・日時・正答率で検索."""
    return await search_created_quizzes(
        user.uid,
        Paging(page=page, size=size),
        query=q,
        resource_id=resource_id,
        sentence_id=sentence_id,
        quiz_types=quiz_types,
        answered=answered,
        created_from=created_from,
        created_to=created_to,
        min_accuracy=min_accuracy,
        max_accuracy=max_accuracy,
    )


@_r.get("/created/resources/{resource_id}/sentences")
async def list_created_quiz_sentences(
    resource_id: UUID,
    user: ActiveUser,
) -> list[SentenceQuizStatus]:
    """Resource内の単文を対象にした作成済みQuiz状況を取得."""
    return await list_created_quiz_sentence_statuses(user.uid, resource_id)


@_r.get("/created/broken")
async def list_broken_created_quizzes(
    user: ActiveUser,
) -> list[BrokenQuizReference]:
    """自分が作成したQuizの修復待ち参照を取得."""
    return await list_broken_created_quiz_references(user.uid)


@_r.get("/created/reports")
async def list_created_quiz_reports(user: ActiveUser) -> list[QuizReport]:
    """自分が作成したQuizへ届いた不備報告を取得."""
    return await list_reported_created_quizzes(user.uid)


@_r.get("/created/unplanned")
async def list_unplanned_created_quizzes_api(
    user: ActiveUser,
) -> list[UnplannedQuiz]:
    """StudyPlanの対象外になっている自分のQuizを取得."""
    return await list_unplanned_created_quizzes(user.uid)


@_r.post("/created/delete")
async def delete_created_quizzes_api(
    param: DeleteQuizzesParam,
    user: ActiveUser,
) -> DeleteQuizzesResult:
    """認証ユーザー自身が作成したQuizを一括削除."""
    return await delete_quizzes(param.quiz_ids, user.uid)


@_r.post("/{quiz_id}/broken/{retired_sentence_id}/reattach")
async def repair_quiz_reference_api(
    quiz_id: UUID,
    retired_sentence_id: UUID,
    param: RepairQuizReferenceParam,
    user: ActiveUser,
) -> QuizReattachmentResult:
    """退役単文へのQuiz参照を選択した現行単文へ付け替える."""
    return await repair_quiz_reference(
        quiz_id,
        retired_sentence_id,
        param.replacement_sentence_id,
        user.uid,
    )


@_r.post("/{quiz_id}/reports", status_code=status.HTTP_204_NO_CONTENT)
async def report_quiz_issue_api(
    quiz_id: UUID,
    param: QuizReportRequest,
    user: ActiveUser,
) -> Response:
    """自他を問わず、表示できたQuizの不備を報告する."""
    receipt = await report_quiz_issue(
        user.uid,
        quiz_id,
        param.reason,
        param.detail,
    )
    if receipt is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    creator_id, created = receipt
    if created and creator_id != user.uid:
        await notify_user(
            creator_id,
            NewNotification(
                kind=NotificationKind.QUIZ_ISSUE_REPORTED,
                title="作成したクイズに不備報告が届きました",
                description=param.detail or "クイズの内容を確認してください。",
                href="/dashboard?view=quiz-management",
            ),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@_r.delete("/{quiz_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quiz_api(
    quiz_id: UUID,
    user: ActiveUser,
) -> Response:
    """認証ユーザー自身が作成したQuizを削除."""
    await delete_quiz(quiz_id, user.uid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@_r.post("/answer/{quiz_id}")
async def answer_quiz_api(
    quiz_id: UUID,
    param: AnswerParam,
    user: ActiveUser,
) -> QuizChain:
    """クイズに回答し、回答結果を含むQuizChainを返す."""
    return await answer_quiz_as_chain(
        quiz_id,
        param.selected,
        user_id=user.uid,
    )


@_r.get("/answer/{quiz_id}")
async def list_answer(
    quiz_id: UUID,
    user: ActiveUser,
) -> Answers:
    """認証ユーザー自身の回答一覧."""
    return await list_answers([quiz_id], user_uid=user.uid)


@_r.get("/answers")
async def list_answer_history_api(
    user: ActiveUser,
    *,
    is_correct: bool | None = None,
    quiz_type: QuizType | None = None,
    resource_id: UUID | None = None,
    page: Annotated[int, Query(gt=0)] = 1,
    size: Annotated[int, Query(gt=0, le=100)] = 20,
) -> AnswerHistoryResult:
    """認証ユーザー自身の回答履歴を新しい順に取得."""
    return await list_answer_history(
        user.uid,
        Paging(page=page, size=size),
        is_correct=is_correct,
        quiz_type=quiz_type,
        resource_id=resource_id,
    )


@_r.get("/learning-progress/{resource_id}")
async def get_learning_progress_api(
    resource_id: UUID,
    user: ActiveUser,
) -> ResourceLearningStatus:
    """所有resourceのクイズ学習進捗を取得."""
    if not await check_entry_owner(user.uid, resource_id):
        msg = "所有していないresourceの学習進捗は取得できません"
        raise NotOwnerError(msg=msg)
    return await fetch_learning_status(resource_id, user.uid)


_r.include_router(study_plan_router())
_r.include_router(quiz_chain_router())


def quiz_router() -> APIRouter:  # noqa: D103
    return _r
