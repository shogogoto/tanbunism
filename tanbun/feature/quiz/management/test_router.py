"""Quiz管理APIのテスト."""

from unittest.mock import AsyncMock

import pytest
from fastapi import status
from httpx import AsyncClient
from neomodel import adb

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.notification.domain import PushSubscriptionDraft
from tanbun.feature.notification.repo import save_push_subscription
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.collections import ReadableQuizResult
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.quiz.learning.study_plan.domain import StudyPlanDraft
from tanbun.feature.quiz.learning.study_plan.repo import create_study_plan
from tanbun.feature.quiz.management.domain import ManagedQuizResult
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aauth_header, aregister

u = async_fixture()(fx_u)


@mark_async_test()
async def test_list_and_delete_created_quizzes_api(ac: AsyncClient, u: LUser):
    """自分が作成したQuizを一覧し、削除できる."""
    n_created = 3
    target = await LSentence.nodes.first(val="ccc")
    own = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    incorrect = await generate_quiz(
        QuizType.SENT2TERM,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    unattempted = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    other = await aregister(email="quiz-management-api-other@ex.com")
    other_quiz = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        other.uid,
    )
    headers = await aauth_header(email=u.email)

    response = await ac.get("/quiz/created", headers=headers)
    result = ReadableQuizResult.model_validate(response.json())

    assert {quiz.quiz_id for quiz in result.data.root} == {
        own.quiz_id,
        incorrect.quiz_id,
        unattempted.quiz_id,
    }

    response = await ac.get("/quiz/created/resources", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    resource_status = response.json()[0]
    assert resource_status["resource"]["name"] == "# title"
    assert resource_status["total_quizzes"] == n_created
    assert resource_status["quiz_counts"] == {"term2sent": 2, "sent2term": 1}

    response = await ac.get(
        "/quiz/created",
        params={"resource_id": target.resource_uid},
        headers=headers,
    )
    filtered = ReadableQuizResult.model_validate(response.json())
    assert len(filtered.data.root) == n_created

    response = await ac.get(
        f"/quiz/created/resources/{target.resource_uid}/sentences",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [
        {
            "sentence_id": str(to_uuid(target.uid)),
            "total_quizzes": 3,
            "quiz_counts": {"term2sent": 2, "sent2term": 1},
        },
    ]

    response = await ac.get(
        "/quiz/created",
        params={"sentence_id": target.uid},
        headers=headers,
    )
    filtered = ReadableQuizResult.model_validate(response.json())
    assert len(filtered.data.root) == n_created

    await ac.post(
        f"/quiz/answer/{own.quiz_id}",
        json={"selected": [str(uid) for uid in own.correct_ids]},
        headers=headers,
    )
    await ac.post(
        f"/quiz/answer/{incorrect.quiz_id}",
        json={"selected": []},
        headers=headers,
    )

    response = await ac.get(
        "/quiz/created/search",
        params={"quiz_types": "sent2term", "answered": True, "max_accuracy": 0.5},
        headers=headers,
    )
    searched = ManagedQuizResult.model_validate(response.json())
    assert searched.total == 1
    assert searched.data[0].quiz.quiz_id == incorrect.quiz_id
    assert searched.data[0].attempts == 1
    assert searched.data[0].accuracy == 0

    response = await ac.get(
        "/quiz/created/search",
        params={"answered": False},
        headers=headers,
    )
    searched = ManagedQuizResult.model_validate(response.json())
    assert [item.quiz.quiz_id for item in searched.data] == [unattempted.quiz_id]

    response = await ac.delete(f"/quiz/{other_quiz.quiz_id}", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND

    response = await ac.delete(f"/quiz/{own.quiz_id}", headers=headers)
    assert response.status_code == status.HTTP_204_NO_CONTENT

    response = await ac.get("/quiz/created", headers=headers)
    result = ReadableQuizResult.model_validate(response.json())
    assert result.total == n_created - 1


@mark_async_test()
async def test_search_created_quizzes_by_text(ac: AsyncClient, u: LUser):
    """問題文は大文字小文字を区別せず部分一致で検索できる."""
    target = await LSentence.nodes.first(val="ccc")
    quiz = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    headers = await aauth_header(email=u.email)

    response = await ac.get(
        "/quiz/created/search",
        params={"q": "CCC"},
        headers=headers,
    )
    searched = ManagedQuizResult.model_validate(response.json())
    assert [item.quiz.quiz_id for item in searched.data] == [quiz.quiz_id]

    response = await ac.get(
        "/quiz/created/search",
        params={"q": "not found in any quiz"},
        headers=headers,
    )
    searched = ManagedQuizResult.model_validate(response.json())
    assert searched.total == 0


@mark_async_test()
async def test_bulk_delete_only_own_created_quizzes(ac: AsyncClient, u: LUser):
    """一括削除は本人のQuizだけを削除し、他人のQuizは読み飛ばす."""
    target = await LSentence.nodes.first(val="ccc")
    own_quizzes = [
        await generate_quiz(
            QuizType.TERM2SENT,
            CandidateType.ALL,
            target.uid,
            3,
            u.uid,
        )
        for _ in range(2)
    ]
    other = await aregister(email="quiz-bulk-delete-other@ex.com")
    other_quiz = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        other.uid,
    )
    headers = await aauth_header(email=u.email)

    response = await ac.post(
        "/quiz/created/delete",
        headers=headers,
        json={
            "quiz_ids": [
                *(str(quiz.quiz_id) for quiz in own_quizzes),
                str(other_quiz.quiz_id),
            ],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "deleted_count": 2,
        "deleted_answer_count": 0,
        "skipped_count": 1,
    }
    other_response = await ac.get(
        "/quiz/created",
        headers=await aauth_header(email=other.email),
    )
    assert other_response.json()["total"] == 1


@mark_async_test()
async def test_list_quizzes_outside_owned_study_plans(ac: AsyncClient, u: LUser):
    """ResourceとQuizTypeのどちらもPlan対象でなければ未所属として返す."""
    target = await LSentence.nodes.first(val="ccc")
    covered = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    unplanned = await generate_quiz(
        QuizType.SENT2TERM,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    await create_study_plan(
        u.uid,
        StudyPlanDraft(
            name="term only",
            resource_ids=[target.resource_uid],
            quiz_types=[QuizType.TERM2SENT],
            n_quiz=1,
            n_option=3,
        ),
    )

    response = await ac.get(
        "/quiz/created/unplanned",
        headers=await aauth_header(email=u.email),
    )

    assert response.status_code == status.HTTP_200_OK
    ids = {item["quiz_id"].replace("-", "") for item in response.json()}
    assert unplanned.quiz_id.hex in ids
    assert covered.quiz_id.hex not in ids


@mark_async_test()
async def test_search_skips_legacy_term_quiz_without_term(
    ac: AsyncClient,
    u: LUser,
):
    """用語を失った旧Quizがあっても管理一覧全体を壊さない."""
    target = await LSentence.nodes.first(val="parent")
    await adb.cypher_query(
        """
        MATCH (user:User {uid: $user_id})
        MATCH (target:Sentence {uid: $target_id})
        CREATE (user)-[:CREATE]->(quiz:Quiz {
            uid: randomUUID(),
            quiz_type: 'TERM2SENT',
            created: datetime()
        })
        CREATE (quiz)-[:QUIZ_TARGET]->(target)
        CREATE (quiz)-[:QUIZ_OPTION]->(target)
        """,
        params={
            "user_id": to_uuid(u.uid).hex,
            "target_id": to_uuid(target.uid).hex,
        },
    )

    response = await ac.get(
        "/quiz/created/search",
        headers=await aauth_header(email=u.email),
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"total": 0, "data": []}


@mark_async_test()
async def test_report_quiz_issue_and_list_for_creator(ac: AsyncClient, u: LUser):
    """他ユーザーの報告を作成者が確認し、再報告では重複しない."""
    target = await LSentence.nodes.first(val="ccc")
    created = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    reporter = await aregister(email="quiz-reporter@ex.com")
    reporter_headers = await aauth_header(email=reporter.email)
    creator_headers = await aauth_header(email=u.email)

    first = await ac.post(
        f"/quiz/{created.quiz_id}/reports",
        headers=reporter_headers,
        json={"reason": "undefined", "detail": "undefinedが含まれる"},
    )
    second = await ac.post(
        f"/quiz/{created.quiz_id}/reports",
        headers=reporter_headers,
        json={"reason": "incorrect", "detail": "正解がおかしい"},
    )
    reports = await ac.get("/quiz/created/reports", headers=creator_headers)
    summary = await ac.get(
        "/quiz/created/issues/summary",
        headers=creator_headers,
    )

    assert first.status_code == status.HTTP_204_NO_CONTENT
    assert second.status_code == status.HTTP_204_NO_CONTENT
    assert reports.status_code == status.HTTP_200_OK
    assert reports.json()[0]["quiz_id"].replace("-", "") == created.quiz_id.hex
    assert reports.json()[0]["quiz"]["statement"]
    assert reports.json()[0]["quiz"]["options"]
    assert reports.json()[0]["quiz"]["correct"]
    assert reports.json()[0]["reason"] == "incorrect"
    assert reports.json()[0]["detail"] == "正解がおかしい"
    assert summary.status_code == status.HTTP_200_OK
    assert summary.json() == {
        "broken_count": 0,
        "reported_count": 1,
        "unplanned_count": 1,
        "total_count": 1,
    }
    assert reports.json()[0]["report_count"] == 1
    notifications = await ac.get("/notifications", headers=creator_headers)
    assert notifications.status_code == status.HTTP_200_OK
    feed = notifications.json()
    assert feed["unread_count"] == 1
    assert feed["notifications"][0]["kind"] == "quiz_issue_reported"
    assert feed["notifications"][0]["href"] == (
        "/dashboard?view=quiz-management&quizMode=issues"
    )


@mark_async_test()
async def test_dismiss_and_reopen_reported_quiz(ac: AsyncClient, u: LUser):
    """問題なしで閉じた報告は一覧から外れ、再報告で要対応へ戻る."""
    expected_notification_count = 2
    target = await LSentence.nodes.first(val="ccc")
    created = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    reporter = await aregister(email="quiz-reopen-reporter@ex.com")
    reporter_headers = await aauth_header(email=reporter.email)
    creator_headers = await aauth_header(email=u.email)

    assert (
        await ac.post(
            f"/quiz/{created.quiz_id}/reports",
            headers=reporter_headers,
            json={"reason": "undefined", "detail": "最初の報告"},
        )
    ).status_code == status.HTTP_204_NO_CONTENT
    assert (
        await ac.post(
            f"/quiz/created/reports/{created.quiz_id}/dismiss",
            headers=creator_headers,
        )
    ).status_code == status.HTTP_204_NO_CONTENT

    assert (await ac.get("/quiz/created/reports", headers=creator_headers)).json() == []
    resolved_reports = await ac.get(
        "/quiz/created/reports",
        params={"status": "resolved"},
        headers=creator_headers,
    )
    assert resolved_reports.status_code == status.HTTP_200_OK
    [resolved_report] = resolved_reports.json()
    assert resolved_report["quiz_id"] == str(created.quiz_id)
    assert resolved_report["detail"] == "最初の報告"
    assert resolved_report["quiz"]["options"]
    assert resolved_report["quiz"]["correct"]
    assert (
        await ac.get(
            "/quiz/created/reports",
            params={"status": "resolved"},
            headers=reporter_headers,
        )
    ).json() == []
    assert (
        await ac.get(
            "/quiz/created/reports",
            params={"status": "invalid"},
            headers=creator_headers,
        )
    ).status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert (
        await ac.get(
            "/quiz/created/issues/summary",
            headers=creator_headers,
        )
    ).json()["reported_count"] == 0

    assert (
        await ac.post(
            f"/quiz/{created.quiz_id}/reports",
            headers=reporter_headers,
            json={"reason": "other", "detail": "再確認してほしい"},
        )
    ).status_code == status.HTTP_204_NO_CONTENT
    reports = await ac.get("/quiz/created/reports", headers=creator_headers)
    notifications = await ac.get("/notifications", headers=creator_headers)

    assert reports.json()[0]["detail"] == "再確認してほしい"
    assert (
        await ac.get(
            "/quiz/created/reports",
            params={"status": "resolved"},
            headers=creator_headers,
        )
    ).json() == []
    assert notifications.json()["unread_count"] == expected_notification_count


@mark_async_test()
async def test_self_report_sends_web_push(
    ac: AsyncClient,
    u: LUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自作Quizの要対応報告も、初回なら本人の端末へPushする."""
    target = await LSentence.nodes.first(val="ccc")
    created = await generate_quiz(
        QuizType.TERM2SENT,
        CandidateType.ALL,
        target.uid,
        3,
        u.uid,
    )
    await save_push_subscription(
        u.uid,
        PushSubscriptionDraft(
            endpoint="https://push.example.test/quiz-self-report",
            keys={"p256dh": "browser-public-key", "auth": "browser-auth-secret"},
        ),
    )
    send = AsyncMock()
    monkeypatch.setattr(
        "tanbun.feature.notification.usecase.webpush_async",
        send,
    )
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "application-server-key")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "private-key")

    response = await ac.post(
        f"/quiz/{created.quiz_id}/reports",
        headers=await aauth_header(email=u.email),
        json={"reason": "incorrect", "detail": "正解を確認したい"},
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    send.assert_awaited_once()
