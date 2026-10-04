"""学習用クイズの補充ユースケース."""

from collections.abc import Iterable

from tanbun.feature.domain.types import UUIDy
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.domain import QuizSource
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.errors import InsufficientOptionsError
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.quiz.learning.selection.domain import QuizFillStrategy
from tanbun.feature.quiz.learning.selection.repo import (
    fetch_target_ids,
    fetch_uncovered_relation_pairs,
)


async def generate_quizzes(  # noqa: PLR0917
    resource_id: UUIDy,
    user_id: UUIDy,
    quiz_type: QuizType,
    strategy: QuizFillStrategy,
    candidate_type: CandidateType,
    n_quiz: int,
    n_option: int,
    *,
    exclude_target_ids: Iterable[UUIDy] | None = None,
) -> list[QuizSource]:
    """戦略に従って対象を選び、複数のクイズを生成."""
    if not quiz_type.has_term:
        pairs = await fetch_uncovered_relation_pairs(
            resource_id,
            user_id,
            quiz_type,
            # 生成不能な関係を延々と試すと推薦API全体が遅くなる。
            limit=max(n_quiz * 10, n_quiz),
            exclude_sent_ids=exclude_target_ids,
        )
        quizzes = []
        generated_target_ids: set[UUIDy] = set()
        option_counts = (
            range(n_option, 1, -1) if quiz_type is QuizType.PAIR2REL else (n_option,)
        )
        for option_count in option_counts:
            for target_id, correct_id in pairs:
                if target_id in generated_target_ids:
                    continue
                try:
                    quiz = await generate_quiz(
                        quiz_type,
                        candidate_type,
                        target_id,
                        option_count,
                        user_id,
                        correct_sent_uids=[correct_id],
                    )
                except InsufficientOptionsError:
                    continue
                quizzes.append(quiz)
                generated_target_ids.add(target_id)
                if len(quizzes) == n_quiz:
                    return quizzes
        return quizzes

    pool, order = strategy.target_selection
    target_ids = await fetch_target_ids(
        resource_id,
        user_id,
        quiz_type,
        pool,
        order,
        # 選択肢不足の候補を飛ばしても指定数へ近づけられるよう余分に取得する。
        limit=max(n_quiz * 10, n_quiz),
        exclude_sent_ids=exclude_target_ids,
    )
    quizzes = []
    for target_id in target_ids:
        try:
            quiz = await generate_quiz(
                quiz_type,
                candidate_type,
                target_id,
                n_option,
                user_id,
            )
        except InsufficientOptionsError:
            continue
        quizzes.append(quiz)
        if len(quizzes) == n_quiz:
            break
    return quizzes
