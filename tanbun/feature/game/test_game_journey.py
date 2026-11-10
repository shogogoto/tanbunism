"""実DBとHTTPを通す、冒険・回答・報酬・装備・再開の縦断テスト."""
# ruff: noqa: PLR2004

from unittest.mock import AsyncMock

from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.fixture import fx_u
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.user.testing import aauth_header

from .balance import GameBalance, update_game_balance
from .catalog import default_catalog, update_game_catalog
from .rewards import GoldSettings, RewardSettings
from .state import GameSave, Run


async def adventure_entry(monkeypatch):
    """本物のリソースとクイズを用意し、乱数に依存しない難易度にする."""
    # 自動出題の予約だけを止め、回答判定・精算・CAS・DB保存は本物を使う。
    monkeypatch.setattr(
        "tanbun.feature.game.router.schedule_dungeon_quizzes",
        AsyncMock(),
    )
    user = await fx_u()
    headers = await aauth_header(user.email)
    await update_game_balance(
        GameBalance(
            base_hp=100,
            base_attack=1000,
            enemy_hp=1,
            power_hp=0,
            enemy_variance_percent=0,
            damage_variance_percent=0,
            min_enemies=1,
            max_encounter_enemies=1,
            encounter_growth_rate=0,
        ),
    )
    catalog = default_catalog()
    catalog.rewards = RewardSettings(
        gold=GoldSettings(base=150, power_coefficient=0, variance_percent=0),
    )
    await update_game_catalog(catalog)
    target = await LSentence.nodes.first(val="ccc")
    quiz = (
        (
            await generate_quiz(
                QuizType.SENT2TERM,
                CandidateType.NEAR,
                target.uid,
                4,
                user.uid,
            )
        )
        .to_readable()
        .model_dump(mode="json")
    )
    [growth] = await fetch_resource_growth(user.uid)
    resource, place = str(growth.resource_id), str(target.uid)
    save = GameSave(
        run=Run(
            resourceId=resource,
            name="本",
            hp=100,
            maxHp=100,
            attack=1000,
            defense=3,
            moves=5,
            kills=0,
            enemyHp=0,
            enemyMaxHp=1,
            quizCursor=0,
            readIds=[place],
            phase="battle",
        ),
        maps={
            resource: {
                "current": place,
                "places": [{"id": place, "region": 0}],
                "edges": [],
            },
        },
        content={
            "quizzes": [quiz],
            "regionEnemies": {
                "0": [{"id": "enemy", "name": "敵", "quizIndex": 0}],
            },
        },
    )

    return headers, save, quiz


@mark_async_test()
async def test_enter_fight_spend_reward_equip_and_reopen(ac, monkeypatch):
    """同じ回答の再送と古い端末の保存で、報酬や装備を壊さない."""
    headers, save, quiz = await adventure_entry(monkeypatch)

    async def request(method, path, body=None):
        response = await ac.request(method, path, headers=headers, json=body)
        assert response.is_success, response.text
        return response.json()

    entered = await request(
        "PUT",
        "/game/state",
        {
            "revision": 0,
            "consume_access": True,
            "save": save.model_dump(mode="json"),
        },
    )
    started = await request(
        "POST",
        "/game/battle/start",
        {
            "revision": entered["revision"],
            "region": 0,
            "scheduled": True,
        },
    )
    marker = started["save"]["battle"]
    enemy = marker["schedule"]["quiz_enemy_id"]
    turn = {
        "battle_id": marker["id"],
        "turn": marker["turn"],
        "answers": {enemy: quiz["correct"]},
    }
    reply = await request("POST", "/game/battle/turn", turn)
    assert reply["results"] == {enemy: True}
    won = reply["state"]
    assert won["save"]["battle"] is None
    assert won["save"]["run"]["hp"] == 100
    assert won["save"]["economy"]["gold"] == 150
    assert (await request("POST", "/game/battle/turn", turn)) == reply
    assert (await adb.cypher_query("MATCH (a:Answer) RETURN count(a)"))[0][0][0] == 1

    # 「次へ」で結果表示を閉じてから、入口のショップを操作する。
    won["save"]["battleFeedback"] = None
    won = await request("PUT", "/game/state", won)

    bought = await request(
        "POST",
        "/game/shop/buy",
        {
            "revision": won["revision"],
            "product_id": "bronze_sword",
        },
    )
    equipped = await request(
        "POST",
        "/game/inventory/equip",
        {
            "revision": bought["revision"],
            "product_id": "bronze_sword",
            "slot": "arm1",
        },
    )
    assert equipped["save"]["run"]["attack"] == 1003
    assert equipped["save"]["economy"]["gold"] == won["save"]["economy"]["gold"] - 100
    assert equipped["save"]["economy"]["equipment"]["arm1"]["item_id"] == "bronze_sword"
    # 未使用品は残回数をまだ保存せず、アイテム定義の使用回数を使う。
    assert equipped["save"]["economy"]["remaining_uses"].get("bronze_sword", 30) == 30
    saved = await request("PUT", "/game/state", equipped)
    reopened = await request("GET", "/game/state")
    assert reopened == saved
    assert reopened["save"]["economy"] == equipped["save"]["economy"]
    assert reopened["save"]["maps"] == equipped["save"]["maps"]
    assert reopened["save"]["run"]["attack"] == 1003
    stale = await ac.put("/game/state", headers=headers, json=entered)
    assert stale.status_code == 409
    assert await request("GET", "/game/state") == reopened
