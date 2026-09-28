"""Quiz管理usecase."""

from tanbun.feature.domain.types import UUIDy
from tanbun.feature.entry.resource.repo.retirement import (
    purge_orphaned_retired_sentences,
)
from tanbun.feature.quiz.management.domain import QuizReattachmentResult
from tanbun.feature.quiz.management.errors import QuizNotFoundError
from tanbun.feature.quiz.management.repo import (
    delete_created_quiz,
    reattach_created_quiz_sentence,
)


async def delete_quiz(
    quiz_id: UUIDy,
    user_id: UUIDy,
) -> None:
    """作成者本人のQuizを削除."""
    if not await delete_created_quiz(quiz_id, user_id):
        msg = f"削除できるQuizが見つかりません: {quiz_id}"
        raise QuizNotFoundError(msg=msg)
    await purge_orphaned_retired_sentences()


async def repair_quiz_reference(
    quiz_id: UUIDy,
    retired_sentence_id: UUIDy,
    replacement_sentence_id: UUIDy,
    user_id: UUIDy,
) -> QuizReattachmentResult:
    """作成者本人が選んだ現行単文へ、壊れたQuiz参照を付け替える."""
    result = await reattach_created_quiz_sentence(
        user_id,
        quiz_id,
        retired_sentence_id,
        replacement_sentence_id,
    )
    if result is None:
        msg = f"修復できるQuiz参照が見つかりません: {quiz_id}"
        raise QuizNotFoundError(msg=msg)
    return result
