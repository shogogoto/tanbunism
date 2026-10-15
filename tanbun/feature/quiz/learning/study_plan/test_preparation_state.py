"""ゲームとStudyPlanは同じ永続ジョブ状態を返す."""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from neomodel import adb
from starlette import status

from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.quiz.learning.fixture import fx_learning, learning_resource_id
from tanbun.feature.user.testing import aauth_header

from . import usecase
from .preparation_state import preparation_states, set_preparation_state
from .repo import ensure_default_resource_study_plan

u = async_fixture()(fx_learning)


@mark_async_test()
@pytest.mark.parametrize("status", ["queued", "running", "failed", "exhausted"])
async def test_both_screens_read_same_persisted_state(ac: AsyncClient, u, status):
    """画面固有の実行フラグやキャッシュに依存しない."""
    resource = await learning_resource_id(u.uid)
    plan = await ensure_default_resource_study_plan(u.uid, resource, "本")
    await set_preparation_state(
        plan.uid,
        u.uid,
        status,
        required=5,
        error="候補不足" if status == "exhausted" else None,
    )
    headers = await aauth_header(u.email)
    study = await ac.get(f"/quiz/study-plans/{plan.uid}/preparation", headers=headers)
    game = await ac.get(f"/game/dungeons/{resource}/preparation", headers=headers)
    assert study.is_success, study.text
    assert game.is_success, game.text
    assert game.json()["preparation"] == study.json()["preparation"]
    assert game.json()["preparation"]["status"] == status


@mark_async_test()
async def test_failure_exhaustion_and_stale_execution_are_visible_and_retryable(
    ac: AsyncClient,
    u,
    monkeypatch,
):
    """失敗を保存し、再試行で候補不足へ更新。古い実行中は失敗扱いにする."""
    resource = await learning_resource_id(u.uid)
    plan = await ensure_default_resource_study_plan(u.uid, resource, "本")
    generate = AsyncMock(side_effect=RuntimeError("生成失敗"))
    monkeypatch.setattr(usecase, "generate_quizzes", generate)
    with pytest.raises(RuntimeError):
        await usecase.prepare_additional_study_plan_quizzes(plan.uid, u.uid, 5)
    assert (await preparation_states(u.uid))[plan.uid.hex].status == "failed"
    headers = await aauth_header(u.email)
    failed = await ac.post(
        f"/quiz/study-plans/{plan.uid}/prepare",
        headers=headers,
        json={"additional_count": 5},
    )
    assert failed.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert (await preparation_states(u.uid))[plan.uid.hex].status == "failed"
    generate.side_effect = None
    generate.return_value = []
    retry = await ac.post(
        f"/game/dungeons/{resource}/preparation/retry",
        headers=headers,
    )
    assert retry.is_success, retry.text
    assert retry.json()["preparation"]["status"] == "queued"
    assert (await preparation_states(u.uid))[plan.uid.hex].status == "exhausted"
    calls = generate.await_count
    await ac.get(f"/game/dungeons/{resource}/preparation", headers=headers)
    assert generate.await_count == calls
    await set_preparation_state(plan.uid, u.uid, "running", required=5)
    await adb.cypher_query(
        """MATCH (p:StudyPlan {uid:$uid})
        SET p.preparation_updated=datetime()-duration('PT16M')""",
        {"uid": plan.uid.hex},
    )
    stale = (await preparation_states(u.uid))[plan.uid.hex]
    assert stale.status == "failed"
    assert "中断" in stale.error
