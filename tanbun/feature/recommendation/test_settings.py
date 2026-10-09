"""復習設定の所有権、日次固定、推薦への適用."""

# ruff: noqa: PLR2004

from datetime import datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.dashboard.repo import list_personal_tanbuns
from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.quiz.answering.repo import create_answer
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.learning.study_plan.domain import StudyPlanDraft
from tanbun.feature.quiz.learning.study_plan.repo import create_study_plan
from tanbun.feature.quiz.listing.test_router import _generate_quizzes
from tanbun.feature.user.label import LUser
from tanbun.feature.user.testing import aauth_header, aregister

from .daily import load_daily, save_daily
from .settings import (
    ReviewSettingsInput,
    get_settings,
    reset_settings,
    save_settings,
    settings_scope,
    today_settings,
)

u = async_fixture()(fx_u)


@mark_async_test()
async def test_plan_scope_preserves_resource_order(u: LUser):
    """推薦用の読み取りでも学習計画の名前と選択順を保持する."""
    _, first = await save_text(u.uid, "# first scope\n  first sentence\n")
    _, second = await save_text(u.uid, "# second scope\n  second sentence\n")
    plan = await create_study_plan(
        u.uid,
        StudyPlanDraft(
            name=" ordered plan ",
            resource_ids=[second.uid, first.uid],
            quiz_types=[QuizType.TERM2SENT],
            n_quiz=5,
            n_option=4,
        ),
    )
    settings = await get_settings(u.uid, f"plan:{plan.uid}")
    assert settings.name == "ordered plan"
    assert settings.resource_ids == [to_uuid(second.uid), to_uuid(first.uid)]


@mark_async_test()
async def test_past_sets_keep_ids_and_progress_without_backdating(
    ac: AsyncClient,
    u: LUser,
):
    """過去セットの消化は実際の日に記録. 未保存の日は生成しない."""
    today = datetime.now(TZ).date()
    yesterday = today - timedelta(days=1)
    quizzes = await _generate_quizzes(u, 2)
    headers = await aauth_header(u.email)
    knowledge = (await ac.get("/dashboard/tanbuns", headers=headers)).json()
    sentence_id = knowledge[0]["uid"]
    await today_settings(u.uid, "default", yesterday)
    await save_daily(
        u.uid,
        settings_scope("default", "tanbuns"),
        yesterday,
        [to_uuid(sentence_id).hex],
    )
    await save_daily(
        u.uid,
        settings_scope("default", "quizzes"),
        yesterday,
        [quizzes[0].quiz_id.hex],
    )
    await ac.post(f"/dashboard/tanbuns/{sentence_id}/exposures", headers=headers)
    await create_answer(quizzes[0].quiz_id, quizzes[0].to_readable().correct, u.uid)
    past = (await ac.get(f"/dashboard/tanbuns?day={yesterday}", headers=headers)).json()
    assert [s["uid"] for s in past] == [sentence_id]
    assert past[0]["seen_today"]
    assert past[0]["seen_in_set"]
    past_quizzes = (
        await ac.get(f"/quiz/daily?day={yesterday}", headers=headers)
    ).json()["data"]
    assert [q["quiz"]["quiz_id"] for q in past_quizzes] == [str(quizzes[0].quiz_id)]
    assert past_quizzes[0]["answered_today"]
    assert past_quizzes[0]["answered_in_set"]
    await reset_settings(u.uid, "default")
    assert await load_daily(u.uid, settings_scope("default", "tanbuns"), yesterday) == [
        to_uuid(sentence_id).hex,
    ]
    missing = today - timedelta(days=2)
    assert (
        await ac.get(f"/dashboard/tanbuns?day={missing}", headers=headers)
    ).json() == []
    assert (await ac.get(f"/quiz/daily?day={missing}", headers=headers)).json()[
        "total"
    ] == 0
    assert (
        await load_daily(u.uid, settings_scope("default", "quizzes"), missing) is None
    )
    for invalid in (today + timedelta(days=1), today - timedelta(days=7)):
        assert (
            await ac.get(f"/dashboard/tanbuns?day={invalid}", headers=headers)
        ).status_code == 422
        assert (
            await ac.get(f"/quiz/daily?day={invalid}", headers=headers)
        ).status_code == 422


@mark_async_test()
async def test_plan_knowledge_review_uses_resources_without_copying_settings(
    ac: AsyncClient,
    u: LUser,
):
    """Plan単位の知識・見たよ・追加復習は対象だけ。別ユーザーは取得不可."""
    _, resource = await save_text(u.uid, "# selected book\n  selected sentence\n")
    plan = await create_study_plan(
        u.uid,
        StudyPlanDraft(
            name="selected plan",
            resource_ids=[resource.uid],
            quiz_types=[QuizType.TERM2SENT],
            n_quiz=5,
            n_option=4,
        ),
    )
    headers = await aauth_header(u.email)
    path = f"/dashboard/tanbuns?profile=plan:{plan.uid}"
    response = await ac.get(path, headers=headers)
    assert response.is_success
    [sentence] = response.json()
    assert sentence["resource_uid"] == str(resource.uid)
    assert sentence["sentence"] == "selected sentence"
    seen = await ac.post(
        f"/dashboard/tanbuns/{sentence['uid']}/exposures",
        headers=headers,
    )
    assert seen.is_success
    assert (await ac.get(path, headers=headers)).json()[0]["seen_today"]
    more = await ac.post(
        f"/dashboard/tanbuns/more?profile=plan:{plan.uid}",
        headers=headers,
    )
    assert {s["resource_uid"] for s in more.json()} == {str(resource.uid)}
    assert len((await ac.get("/review/settings", headers=headers)).json()) == 1
    other = await aregister("other-plan-review@example.com")
    assert (
        await ac.get(path, headers=await aauth_header(other.email))
    ).status_code == 404
    assert (
        await ac.get("/dashboard/tanbuns?profile=plan:invalid", headers=headers)
    ).status_code == 404


@mark_async_test()
async def test_settings_api_ownership_and_validation(ac: AsyncClient, u: LUser):
    """他人の設定やResourceは編集不可. 標準は削除不可."""
    url = "/review/settings"
    assert (await ac.get(url)).status_code == 401
    headers = await aauth_header(u.email)
    initial = await ac.get(url, headers=headers)
    assert initial.json()[0]["id"] == "default"
    data = {"name": "苦手", "tanbun_count": 4, "quiz_count": 3, "priority": "weak"}
    created = await ac.post(url, json=data, headers=headers)
    assert created.is_success
    profile_id = created.json()["id"]
    other = await aregister("other-review@example.com")
    other_headers = await aauth_header(other.email)
    assert (
        await ac.put(f"{url}/{profile_id}", json=data, headers=other_headers)
    ).status_code == 404
    assert (
        await ac.post(f"{url}/{profile_id}/rebuild", headers=other_headers)
    ).status_code == 404
    assert (await ac.delete(f"{url}/default", headers=headers)).status_code == 400
    assert (
        await ac.post(
            url,
            json={**data, "resource_ids": [str(uuid4())]},
            headers=headers,
        )
    ).status_code == 403
    assert (
        await ac.post(url, json={**data, "resource_ids": []}, headers=headers)
    ).status_code == 422
    assert (
        await ac.post(url, json={**data, "quiz_count": 1000}, headers=headers)
    ).status_code == 422
    assert (await ac.delete(f"{url}/{profile_id}", headers=headers)).status_code == 204


@mark_async_test()
async def test_settings_frozen_today_and_explicit_rebuild(u: LUser):
    """編集では今日の件数を変えず、再作成・翌日に最新設定を使う."""
    day = datetime.now(TZ).date()
    setting = await save_settings(
        u.uid,
        ReviewSettingsInput(name="少し", tanbun_count=2),
    )
    first = await list_personal_tanbuns(u.uid, day, profile_id=setting.id)
    assert len(first) == 2
    await save_settings(
        u.uid,
        ReviewSettingsInput(name="少し", tanbun_count=5),
        setting.id,
    )
    assert await list_personal_tanbuns(u.uid, day, profile_id=setting.id) == first
    assert (await today_settings(u.uid, setting.id, day)).tanbun_count == 2
    assert (
        await today_settings(u.uid, setting.id, day + timedelta(days=1))
    ).tanbun_count == 5
    await reset_settings(u.uid, setting.id)
    assert len(await list_personal_tanbuns(u.uid, day, profile_id=setting.id)) == 5


@mark_async_test()
async def test_resource_filter_and_quiz_count(ac: AsyncClient, u: LUser):
    """対象Resourceを選択し、単文・クイズ件数を別々に適用."""
    from tanbun.feature.entry.resource.usecase import save_text  # noqa: PLC0415

    await _generate_quizzes(u, 5)
    _, resource = await save_text(
        u.uid,
        "# only-this\n  first sentence\n  second sentence\n",
    )
    headers = await aauth_header(u.email)
    setting = await save_settings(
        u.uid,
        ReviewSettingsInput(
            name="この本",
            resource_ids=[to_uuid(resource.uid)],
            tanbun_count=1,
            quiz_count=2,
        ),
    )
    sentences = await ac.get(
        f"/dashboard/tanbuns?profile={setting.id}",
        headers=headers,
    )
    assert len(sentences.json()) == 1
    assert sentences.json()[0]["resource_uid"] == str(resource.uid)
    quizzes = await ac.get(f"/quiz/daily?profile={setting.id}", headers=headers)
    assert quizzes.json()["total"] == 0
    await save_settings(
        u.uid,
        ReviewSettingsInput(name="標準", quiz_count=2),
        "default",
    )
    default = await ac.get("/quiz/daily", headers=headers)
    assert default.json()["total"] == 2
    score = await save_settings(
        u.uid,
        ReviewSettingsInput(name="重要", quiz_count=3, priority="score"),
    )
    assert (await ac.get(f"/quiz/daily?profile={score.id}", headers=headers)).json()[
        "total"
    ] == 3


@mark_async_test()
async def test_more_keeps_today_prefix_and_does_not_generate(ac: AsyncClient, u: LUser):
    """追加復習でも順序を保ち、同じIDを重複追加しない."""
    await _generate_quizzes(u, 5)
    await save_settings(
        u.uid,
        ReviewSettingsInput(name="少し", tanbun_count=2, quiz_count=2),
        "default",
    )
    headers = await aauth_header(u.email)
    first = (await ac.get("/dashboard/tanbuns", headers=headers)).json()
    more = (await ac.post("/dashboard/tanbuns/more", headers=headers)).json()
    assert [s["uid"] for s in more[:2]] == [s["uid"] for s in first]
    assert len(more) == 4
    assert len({s["uid"] for s in more}) == len(more)
    first_quizzes = (await ac.get("/quiz/daily", headers=headers)).json()["data"]
    more_quizzes = (await ac.post("/quiz/daily/more", headers=headers)).json()["data"]
    assert [q["quiz"]["quiz_id"] for q in more_quizzes[:2]] == [
        q["quiz"]["quiz_id"] for q in first_quizzes
    ]
    assert len(more_quizzes) == 4
    assert (await ac.post("/quiz/daily/more", headers=headers)).json()["total"] == 5
    assert (await ac.get("/quiz/daily", headers=headers)).json()["total"] == 5
