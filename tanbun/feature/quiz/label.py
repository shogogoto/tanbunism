"""neomodel label."""

from neomodel import (
    AsyncOne,
    AsyncRelationshipManager,
    AsyncRelationshipTo,
    AsyncStructuredNode,
    DateTimeProperty,
    StringProperty,
    UniqueIdProperty,
)

from tanbun.feature.quiz.domain.domain import QuizType


class LQuiz(AsyncStructuredNode):
    """クイズ.

    ここの情報だけから問題を生成できる
    """

    __label__ = "Quiz"
    uid = UniqueIdProperty()
    quiz_type = StringProperty(
        required=True,
        choices=((quiz_type.value, quiz_type.name) for quiz_type in QuizType),
    )
    created = DateTimeProperty()

    # 単文ネットワークを中心としているので用語を指すつもりであっても、その単文を指すべし
    target: AsyncRelationshipManager = AsyncRelationshipTo(  # type: ignore  # noqa: PGH003
        "LSentence",
        "QUIZ_TARGET",
        cardinality=AsyncOne,
    )

    # 誤答肢を指す
    option: AsyncRelationshipManager = AsyncRelationshipTo(  # type: ignore  # noqa: PGH003
        "LSentence",
        "QUIZ_OPTION",
    )

    correct: AsyncRelationshipManager = AsyncRelationshipTo(  # type: ignore  # noqa: PGH003
        "LAnswer",
        "QUIZ_CORRECT",
    )


class LAnswer(AsyncStructuredNode):
    """回答."""

    __label__ = "Answer"
    uid = UniqueIdProperty()
    created = DateTimeProperty()

    answer_of: AsyncRelationshipManager = AsyncRelationshipTo(  # type: ignore  # noqa: PGH003
        "LQuiz",
        "ANSWER_OF",
        cardinality=AsyncOne,
    )

    select: AsyncRelationshipManager = AsyncRelationshipTo(  # type: ignore  # noqa: PGH003
        "LSentence",
        "SELECT",
    )
