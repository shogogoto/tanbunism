"""ショップ・装備・歩数回復と日次報酬の保存規則."""
# ruff: noqa: PLR2004

import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.domain.datetime import TZ
from tanbun.feature.recommendation.daily import save_daily
from tanbun.feature.recommendation.settings import settings_scope
from tanbun.feature.user.label import LUser

from .balance import Allocation, GameBalance
from .economy import EconomyAction, change_economy, grant_review_reward
from .inventory import Economy, equipped_stats
from .state import GameSave, GameState, Run, StateUpdate, read_state, write_state


async def seed_user() -> LUser:
    """所持金と休憩中の状態を直接fixtureとして保存."""
    user = await LUser(email="shop@example.com").save()
    save = GameSave(
        economy=Economy(gold=200, inventory={"step_potion": 1}),
        run=Run(
            resourceId="book",
            name="本",
            hp=10,
            maxHp=35,
            attack=10,
            defense=1,
            moves=5,
            kills=0,
            enemyHp=0,
            enemyMaxHp=1,
            quizCursor=0,
            readIds=[],
            phase="rest",
        ),
    )
    await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) SET u.game_state=$state, u.game_revision=0",
        {"uid": user.uid, "state": GameState(save=save).model_dump_json()},
    )
    return user


@mark_async_test()
async def test_purchase_equip_and_stale_retry():
    """一回の支払い、装備の加算、旧revisionと不足金の拒否."""
    user = await seed_user()
    request = EconomyAction(revision=0, product_id="bronze_sword")
    bought = await change_economy(user.uid, request, "buy")
    assert bought.save.economy.gold == 100
    assert bought.save.economy.inventory["bronze_sword"] == 1
    with pytest.raises(HTTPException) as stale:
        await change_economy(user.uid, request, "buy")
    assert stale.value.status_code == 409
    equipped = await change_economy(
        user.uid,
        EconomyAction(revision=1, product_id="bronze_sword"),
        "equip",
    )
    assert equipped.save.run.attack == 13
    assert equipped.save.run.hp == 10
    assert equipped.save.economy.weapon_attack == 3
    with pytest.raises(HTTPException) as insufficient:
        await change_economy(
            user.uid,
            EconomyAction(revision=2, product_id="steel_sword"),
            "buy",
        )
    assert insufficient.value.status_code == 422
    latest = await read_state(user.uid)
    assert latest.save.economy.gold == 100
    await change_economy(
        user.uid,
        EconomyAction(revision=2, product_id="healing_potion"),
        "buy",
    )
    healed = await change_economy(
        user.uid,
        EconomyAction(revision=3, product_id="healing_potion"),
        "use",
    )
    assert healed.save.run.hp == 30
    assert healed.save.economy.gold == 70
    assert healed.save.economy.inventory["healing_potion"] == 0


@mark_async_test()
async def test_step_potion_resumes_without_consuming_clock_access():
    """休憩から3歩復帰。HP・現在地は変えず、満タンでは消費しない."""
    user = await seed_user()
    updated = await change_economy(
        user.uid,
        EconomyAction(revision=0, product_id="step_potion"),
        "use",
    )
    assert updated.save.run.moves == 2
    assert updated.save.run.phase == "path"
    assert updated.save.run.hp == 10
    assert updated.save.economy.inventory["step_potion"] == 0
    assert (
        await adb.cypher_query(
            "MATCH (u:User {uid:$uid}) RETURN u.adventure_consumed_slot",
            {"uid": user.uid},
        )
    )[0][0][0] == -1


@mark_async_test()
async def test_snapshots_cannot_mint_gold_items_or_change_equipment():
    """一般のゲーム保存・旧クライアントで所持品は改変・削除できない."""
    user = await seed_user()
    previous = await read_state(user.uid)
    save = previous.save.model_copy(deep=True)
    save.economy = Economy(
        gold=9999,
        inventory={"steel_sword": 1},
        weapon="steel_sword",
    )
    updated = await write_state(user.uid, StateUpdate(revision=0, save=save))
    assert updated.save.economy == previous.save.economy


@mark_async_test()
async def test_review_completion_awards_once_per_day_even_concurrently():
    """保存済みの今日のセット全問を見たときのみ付与。再送は冪等."""
    user = await seed_user()
    day = datetime.now(TZ).date()
    await save_daily(user.uid, settings_scope("default", "tanbuns"), day, ["a", "b"])
    await adb.cypher_query(
        """CREATE (:TanbunExposure {key:$key, user_id:$uid,
        seen_on:date($day), sentence_id:'a'})""",
        {"uid": user.uid, "key": "seen-a", "day": day.isoformat()},
    )
    assert not (await grant_review_reward(user.uid))[1]
    await adb.cypher_query(
        """CREATE (:TanbunExposure {key:$key, user_id:$uid,
        seen_on:date($day), sentence_id:'b'})""",
        {"uid": user.uid, "key": "seen-b", "day": day.isoformat()},
    )
    results = await asyncio.gather(*(grant_review_reward(user.uid) for _ in range(2)))
    assert sum(awarded for _state, awarded in results) == 1
    final, awarded = await grant_review_reward(user.uid)
    assert not awarded
    assert final.save.economy.inventory["step_potion"] == 2
    assert final.save.economy.review_reward_day == day.isoformat()


def test_equipment_bonus_is_derived_from_owned_weapon():
    """装備IDだけでは攻撃力を付けない。加算はallocationと独立."""
    economy = Economy(inventory={"steel_sword": 1}, weapon="steel_sword")
    assert equipped_stats(Allocation(attack=2), GameBalance(), economy)["attack"] == 22
    economy.inventory.clear()
    assert equipped_stats(Allocation(attack=2), GameBalance(), economy)["attack"] == 14
