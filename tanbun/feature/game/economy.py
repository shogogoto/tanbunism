"""撃破報酬・ショップ・復習報酬をrevision付きで保存する."""

from asyncio import sleep
from datetime import datetime, timedelta

from fastapi import HTTPException
from neo4j.exceptions import TransientError
from neomodel import adb
from pydantic import BaseModel, Field

from tanbun.config.transactions import atomic
from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.recommendation.daily import load_daily
from tanbun.feature.recommendation.settings import settings_scope

from .balance import get_game_balance
from .catalog import GameCatalog, get_game_catalog
from .inventory import Economy, Product, ProductId, equipped_stats
from .state import GameSave, GameState, StateUpdate, compare_and_save, read_state

MOVES_PER_EVENT = 5
GOLD_PER_LEVEL = 10


class EconomyAction(BaseModel):
    """同じ画面の二重操作・古い画面からの消費を防ぐ."""

    revision: int = Field(ge=0)
    product_id: ProductId


def catalog_products(
    catalog: GameCatalog,
    *,
    include_disabled: bool = False,
) -> list[Product]:
    """既存のショップ表示形式を保ち、効果はスキルから導出する。."""
    skills = {s.id: s for s in catalog.skills}
    kinds = {
        "equip_attack": "weapon",
        "heal_hp": "hp",
        "heal_mp": "mp",
        "recover_moves": "moves",
        "attack": "skill",
        "buff": "skill",
        "debuff": "skill",
        "ailment": "skill",
    }
    return [
        Product(
            id=i.id,
            name=i.name,
            price=i.price if i.enabled and skills[i.skill_id].enabled else None,
            kind=kinds[skills[i.skill_id].effect],
            amount=skills[i.skill_id].amount,
            amount_mode=skills[i.skill_id].amount_mode,
            icon=i.icon,
            skill_id=i.skill_id,
            skill_name=skills[i.skill_id].name,
            enabled=i.enabled and skills[i.skill_id].enabled,
        )
        for i in catalog.items
        if include_disabled or (i.enabled and skills[i.skill_id].enabled)
    ]


async def review_progress(user_id: UUIDy) -> dict:
    """今日の標準セットの完了数。ダンジョン閲覧も同じ記録で判定する."""
    day = datetime.now(TZ).date()
    ids = await load_daily(
        to_uuid(user_id).hex,
        settings_scope("default", "tanbuns"),
        day,
    )
    if not ids:
        return {"day": day.isoformat(), "done": 0, "total": 0}
    rows, _ = await adb.cypher_query(
        """MATCH (e:TanbunExposure {user_id:$uid, seen_on:date($day)})
        WHERE e.sentence_id IN $ids RETURN count(DISTINCT e.sentence_id)""",
        {"uid": to_uuid(user_id).hex, "day": day.isoformat(), "ids": ids},
    )
    return {
        "day": day.isoformat(),
        "done": rows[0][0] if rows else 0,
        "total": len(set(ids)),
    }


async def quiz_review_progress(user_id: UUIDy) -> dict:
    """日本時間の今日、復習画面で回答した異なる20問を判定する."""
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    rows, _ = await adb.cypher_query(
        """MATCH (u:User {uid:$uid})-[:ANSWER]->(a:Answer {source:'review'})
        -[:ANSWER_OF]->(q:Quiz)
        WHERE a.created >= datetime($start) AND a.created < datetime($end)
        RETURN count(DISTINCT q.uid)""",
        {
            "uid": to_uuid(user_id).hex,
            "start": start.isoformat(),
            "end": (start + timedelta(days=1)).isoformat(),
        },
    )
    return {
        "day": start.date().isoformat(),
        "done": rows[0][0] if rows else 0,
        "total": 20,
    }


async def grant_review_reward(
    user_id: UUIDy,
    *,
    quiz: bool = False,
) -> tuple[GameState, bool]:
    """1日1個。同時達成や再読み込みでも二重付与しない."""
    previous = await read_state(user_id)
    marker = "quiz_reward_day" if quiz else "review_reward_day"
    if getattr(previous.save.economy, marker) == datetime.now(TZ).date().isoformat():
        return previous, False
    progress = (
        await quiz_review_progress(user_id) if quiz else await review_progress(user_id)
    )
    for attempt in range(3):
        if (
            not progress["total"]
            or progress["done"] < progress["total"]
            or getattr(previous.save.economy, marker) == progress["day"]
        ):
            return previous, False
        updated = previous.model_copy(deep=True)
        items = updated.save.economy.inventory
        items["step_potion"] = items.get("step_potion", 0) + 1
        setattr(updated.save.economy, marker, progress["day"])
        updated.revision += 1
        owns_transaction = adb._active_transaction is None  # noqa: SLF001
        try:
            async with atomic():
                rows = await compare_and_save(
                    user_id,
                    StateUpdate(revision=previous.revision, save=updated.save),
                    updated,
                )
        except TransientError as cause:
            # atomic() has rolled back a transaction we own. An enclosing HTTP
            # transaction must instead fail as a whole; never reuse it here.
            if (
                cause.code != "Neo.TransientError.Transaction.DeadlockDetected"
                or not owns_transaction
            ):
                raise
            rows = []
            await sleep(0.05 * (attempt + 1))
        if rows:
            return updated, True
        previous = await read_state(user_id)
    # The exposure is already recorded. The next state read retries the reward.
    return await read_state(user_id), False


async def change_economy(user_id: UUIDy, body: EconomyAction, action: str) -> GameState:
    """購入・装備・消費は同じCASで行い、戦闘中は変更しない."""
    previous = await read_state(user_id)
    if previous.revision != body.revision:
        raise HTTPException(409, "所持品が更新されました。もう一度操作してください。")
    if (
        previous.save.battleFeedback
        or previous.save.battle
        or (previous.save.run and previous.save.run.phase == "battle")
    ):
        raise HTTPException(409, "戦闘中はショップ・所持品を操作できません。")
    updated = previous.model_copy(deep=True)
    economy = updated.save.economy
    product = await active_product(
        body.product_id,
        allow_disabled=action == "equip" and economy.weapon == body.product_id,
    )
    quantity = economy.inventory.get(product.id, 0)
    if action == "buy":
        buy(economy, product)
    elif action == "equip":
        if product.kind != "weapon" or not quantity:
            raise HTTPException(422, "所持している武器を選んでください。")
        economy.weapon = None if economy.weapon == product.id else product.id
        economy.equipment_attack = product.amount if economy.weapon else 0
    else:
        use_item(updated.save, product)
    stats = equipped_stats(updated.save.allocation, await get_game_balance(), economy)
    for current in [updated.save.run, *(d.run for d in updated.save.dungeons.values())]:
        if current:
            for key, value in stats.items():
                setattr(current, key, value)
            current.hp = min(current.hp, current.maxHp)
    updated.revision += 1
    async with atomic():
        rows = await compare_and_save(
            user_id,
            StateUpdate(revision=previous.revision, save=updated.save),
            updated,
        )
        if not rows:
            raise HTTPException(
                409,
                "所持品が更新されました。もう一度操作してください。",
            )
    return updated


async def active_product(product_id: str, *, allow_disabled: bool = False) -> Product:
    """有効な定義だけを購入・使用する."""
    product = next(
        (
            p
            for p in catalog_products(
                await get_game_catalog(),
                include_disabled=allow_disabled,
            )
            if p.id == product_id
        ),
        None,
    )
    if product is None:
        raise HTTPException(422, "このアイテムは現在利用できません。")
    return product


def buy(economy: Economy, product: Product) -> None:
    """支払いと個数を一緒に更新する."""
    if product.price is None:
        raise HTTPException(422, "このアイテムは復習の達成報酬です。")
    if economy.gold < product.price:
        raise HTTPException(422, "ゴールドが不足しています。")
    quantity = economy.inventory.get(product.id, 0)
    if product.kind == "weapon" and quantity:
        raise HTTPException(422, "この武器は既に持っています。")
    economy.gold -= product.price
    economy.inventory[product.id] = quantity + 1


def use_item(save: GameSave, product: Product) -> None:
    """残量のある回復だけを許可し、1個消費する."""
    run = save.run
    quantity = save.economy.inventory.get(product.id, 0)
    if not quantity or product.kind == "weapon" or not run:
        raise HTTPException(422, "このアイテムは使用できません。")
    if product.kind == "moves":
        if not run.moves:
            raise HTTPException(422, "歩数は満タンです。")
        remaining_cost = max(0, run.moves - product.amount)
        for current in [run, *(d.run for d in save.dungeons.values())]:
            current.moves = remaining_cost
            if current.phase == "rest" and current.hp:
                current.phase = "path"
    else:
        restore_energy(run, product)
        if run.phase == "defeated" and run.hp:
            run.phase = "rest" if run.moves >= MOVES_PER_EVENT else "path"
    save.economy.inventory[product.id] = quantity - 1


def restore_energy(run, product: Product) -> None:
    """戦闘用アイテムを平時に誤使用せず、回復量は上限に収める."""
    if product.kind == "skill":
        raise HTTPException(422, "このアイテムは戦闘のガンビットで使用します。")
    current, maximum = (
        (run.mp, run.maxMp) if product.kind == "mp" else (run.hp, run.maxHp)
    )
    if current >= maximum:
        raise HTTPException(
            422,
            "MPは満タンです。" if product.kind == "mp" else "HPは満タンです。",
        )
    setattr(
        run,
        "mp" if product.kind == "mp" else "hp",
        min(
            maximum,
            current
            + (
                maximum * product.amount // 100
                if product.amount_mode == "percent"
                else product.amount
            ),
        ),
    )
