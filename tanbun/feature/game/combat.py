"""操作はブラウザ、戦闘の開始・精算・撤退はrevision付きで保存する."""

import json
from random import sample
from time import time
from uuid import uuid4

from fastapi import HTTPException
from neomodel import adb
from pydantic import BaseModel, Field

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.gamification.usecase import fetch_learning_progress
from tanbun.feature.notification.usecase import dispatch_saved_notifications
from tanbun.feature.quiz.answering.repo import create_answer_in_transaction
from tanbun.feature.quiz.chain.usecase import expand_quiz_chain

from .balance import Allocation, GameBalance, get_game_balance
from .state import BattleMarker, GameState, StateUpdate, compare_and_save, read_state

MOVES_PER_EVENT = 5
ENEMIES_TO_CLEAR = 3


class EncounterRequest(BaseModel):
    """移動の保存後に遭遇を確定する。checkpointは実際のマップ上に限る."""

    revision: int = Field(ge=0)
    region: int = Field(ge=0, le=10000)
    checkpoint: str = Field(default="@entrance", max_length=64)


class TurnRequest(BaseModel):
    """敵HPは送信も保存もしない。撃破判定はブラウザのゲーム状態."""

    battle_id: str = Field(max_length=64)
    turn: int = Field(ge=0)
    answers: dict[str, list[str]] = Field(default_factory=dict, max_length=20)
    defeated: list[str] = Field(default_factory=list, max_length=20)
    retreat: bool = False


class EnemyStats(BaseModel):
    """固定IDと固定クイズから、その時点の能力を導出する."""

    id: str
    name: str
    quizIndex: int  # noqa: N815
    hp: int
    attack: int
    relations: int
    region: int


class CombatContext(BaseModel):
    """敵の能力は保存した値ではなく最新のPowerと関係数を使う."""

    balance: GameBalance
    enemies: list[EnemyStats]


async def combat_context(
    user_id: UUIDy,
    state: GameState | None = None,
) -> CombatContext:
    """固定された全領域の敵をまとめて評価。イベントや秒ごとの問い合わせはしない."""
    state = state or await read_state(user_id)
    balance = await get_game_balance()
    run, content = state.save.run, state.save.content or {}
    if not run:
        return CombatContext(balance=balance, enemies=[])
    growth = await fetch_resource_growth(user_id, only_resource=run.resourceId)
    power = growth[0].power if growth else 0
    quizzes = content.get("quizzes", [])
    ids = [to_uuid(q["quiz_id"]).hex for q in quizzes]
    rows, _ = await adb.cypher_query(
        """UNWIND $ids AS id
        MATCH (q:Quiz {uid:id})-[:QUIZ_TARGET]->(s:Sentence {resource_uid:$resource})
        OPTIONAL MATCH (s)-[r:TO|REF|RESOLVED|QUOTERM|EXAMPLE|BELOW]-(other)
        WHERE other <> s AND other.val <> '<<<not defined>>>'
        RETURN q.uid, count(DISTINCT r)""",
        {"ids": ids, "resource": to_uuid(run.resourceId).hex},
    )
    relations = dict(rows)
    return CombatContext(
        balance=balance,
        enemies=derive_enemies(content, relations, power, balance),
    )


def derive_enemies(
    content: dict,
    relations: dict,
    power: int,
    balance: GameBalance,
) -> list[EnemyStats]:
    """旧snapshotのHP/攻は無視し、同じIDの能力をその都度導出する."""
    quizzes = content.get("quizzes", [])
    enemies = []
    for key, pool in content.get("regionEnemies", {}).items():
        region = int(key)
        for item in pool:
            index = item.get("quizIndex", -1)
            if not 0 <= index < len(quizzes):
                continue
            uid = to_uuid(quizzes[index]["quiz_id"]).hex
            if uid not in relations:
                continue
            hp, attack = balance.enemy_stats(power, relations[uid], region)
            enemies.append(
                EnemyStats(
                    id=item["id"],
                    name=item["name"],
                    quizIndex=index,
                    hp=hp,
                    attack=attack,
                    relations=relations[uid],
                    region=region,
                ),
            )
    return enemies


async def persist(user_id: UUIDy, previous: GameState, updated: GameState) -> None:
    """トランザクション内のCAS。回答履歴も同じcommitにまとめる."""
    updated.revision = previous.revision + 1
    rows = await compare_and_save(
        user_id,
        StateUpdate(revision=previous.revision, save=updated.save),
        updated,
    )
    if not rows:
        raise HTTPException(409, "別の画面で冒険が更新されました。")


async def start_combat(user_id: UUIDy, body: EncounterRequest) -> GameState:
    """重複しない固定クイズの敵を達成度に応じて抽選する."""
    previous = await read_state(user_id)
    run = previous.save.run
    if (
        previous.revision != body.revision
        or not run
        or run.phase != "battle"
        or previous.save.battle
    ):
        raise HTTPException(409, "遭遇状態が変更されました。")
    dungeon = previous.save.maps.get(run.resourceId)
    if dungeon and body.checkpoint not in {
        "@entrance",
        *(p.id for p in dungeon.places),
    }:
        raise HTTPException(422, "撤退先がマップにありません。")
    context = await combat_context(user_id, previous)
    unique = {
        to_uuid(previous.save.content["quizzes"][enemy.quizIndex]["quiz_id"]).hex: enemy
        for enemy in context.enemies
        if enemy.region == body.region
    }
    if not unique:
        raise HTTPException(
            422,
            "有効な戦闘クイズがありません。候補を更新してください。",
        )
    count = min(
        len(unique),
        context.balance.max_enemies,
        1 + body.region // context.balance.regions_per_enemy,
    )
    chosen = sample(list(unique.values()), count)
    updated = previous.model_copy(deep=True)
    updated.save.battle = BattleMarker(
        id=uuid4().hex,
        region=body.region,
        checkpoint=body.checkpoint,
        enemies=[e.id for e in chosen],
    )
    stats = updated.save.allocation.stats(context.balance)
    next_run = updated.save.run
    for key, value in stats.items():
        setattr(next_run, key, value)
    next_run.hp = min(next_run.hp, next_run.maxHp)
    next_run.enemyHp = (
        0  # Legacy fields remain for old snapshots, never store combat HP.
    )
    next_run.enemyMaxHp = 1
    next_run.answerDeadline = int(time() * 1000) + stats["answerSeconds"] * 1000
    async with adb.transaction:
        await persist(user_id, previous, updated)
    return updated


async def read_receipt(user_id: UUIDy, body: TurnRequest) -> dict | None:
    """応答喪失による再送でも二重回答・二重XPにしない."""
    rows, _ = await adb.cypher_query(
        "MATCH (u:User {uid:$uid}) RETURN u.game_turn_receipt",
        {"uid": to_uuid(user_id).hex},
    )
    receipt = json.loads(rows[0][0]) if rows and rows[0][0] else None
    return (
        receipt
        if receipt and receipt["id"] == f"{body.battle_id}:{body.turn}"
        else None
    )


async def settle_turn(user_id: UUIDy, body: TurnRequest) -> dict:
    """正誤・ダメージ・回答履歴をまとめて確定する。未回答にはXPを付けない."""
    if receipt := await read_receipt(user_id, body):
        return {
            **receipt,
            "state": (await read_state(user_id)).model_dump(by_alias=True),
        }
    previous = await read_state(user_id)
    marker, run = previous.save.battle, previous.save.run
    if not marker or not run or (marker.id, marker.turn) != (body.battle_id, body.turn):
        raise HTTPException(409, "戦闘ターンが変更されました。再読み込みしてください。")
    if not set(body.answers) <= set(marker.enemies) or not set(body.defeated) <= set(
        marker.enemies,
    ):
        raise HTTPException(422, "この戦闘にいない敵です。")
    context = await combat_context(user_id, previous)
    enemies = {enemy.id: enemy for enemy in context.enemies}
    results, selected_quizzes = await evaluate_answers(user_id, previous, body, enemies)
    if any(not results.get(enemy_id) for enemy_id in body.defeated):
        raise HTTPException(422, "未正解の敵は撃破できません。")
    stats = previous.save.allocation.stats(context.balance)
    damage = sum(
        max(
            1,
            (enemies[e].attack if e in enemies else context.balance.enemy_attack)
            - stats["defense"],
        )
        for e in marker.enemies
        if not results.get(e)
    )
    updated = apply_turn_result(previous, body, results, damage, stats)
    receipt = {"id": f"{marker.id}:{marker.turn}", "results": results, "damage": damage}
    notifications = []
    async with adb.transaction:
        await persist(user_id, previous, updated)
        for enemy_id, uid in selected_quizzes.items():
            _, saved = await create_answer_in_transaction(
                uid,
                body.answers[enemy_id],
                user_id,
                is_correct=results[enemy_id],
            )
            notifications.extend(saved)
        await adb.cypher_query(
            "MATCH (u:User {uid:$uid}) SET u.game_turn_receipt=$receipt",
            {"uid": to_uuid(user_id).hex, "receipt": json.dumps(receipt)},
        )
    await dispatch_saved_notifications(user_id, notifications)
    return {**receipt, "state": updated.model_dump(by_alias=True)}


async def evaluate_answers(
    user_id: UUIDy,
    previous: GameState,
    body: TurnRequest,
    enemies: dict,
) -> tuple[dict, dict]:
    """既存のクイズ閲覧条件と正誤判定を共通利用する."""
    quizzes = (previous.save.content or {}).get("quizzes", [])
    results, selected_quizzes = {}, {}
    # Reuse assignment/visibility validation and current quiz correctness.
    for enemy_id, selected in body.answers.items():
        enemy = enemies.get(enemy_id)
        if not enemy:
            raise HTTPException(
                422,
                "クイズが削除・更新されています。撤退してください。",
            )
        uid = to_uuid(quizzes[enemy.quizIndex]["quiz_id"])
        chain = await expand_quiz_chain(user_id, uid)
        readable = chain.quizzes[0].readable
        if not set(selected) <= set(readable.options):
            raise HTTPException(422, "クイズに存在しない選択肢です。")
        results[enemy_id] = readable.is_correct(selected)
        selected_quizzes[enemy_id] = uid
    return results, selected_quizzes


def apply_turn_result(
    previous: GameState,
    body: TurnRequest,
    results: dict[str, bool],
    damage: int,
    stats: dict,
) -> GameState:
    """相打ちの敗北優先・撤退先・歩数維持を一箇所で扱う."""
    marker, run = previous.save.battle, previous.save.run
    if not marker or not run:
        msg = "Missing battle"
        raise ValueError(msg)
    updated = previous.model_copy(deep=True)
    next_run = updated.save.run
    for key, value in stats.items():
        setattr(next_run, key, value)
    next_run.hp = max(0, min(next_run.hp, next_run.maxHp) - damage)
    next_run.kills += len(set(body.defeated))
    next_run.quizCursor += len(body.answers)
    survivors = [e for e in marker.enemies if e not in body.defeated]
    next_run.phase = (
        "defeated"
        if not next_run.hp
        else "rest"
        if body.retreat and next_run.moves >= MOVES_PER_EVENT
        else "path"
        if body.retreat
        else "battle"
        if survivors
        else "cleared"
        if next_run.kills >= ENEMIES_TO_CLEAR
        else "rest"
        if next_run.moves >= MOVES_PER_EVENT
        else "path"
    )
    if body.retreat or not next_run.hp:
        dungeon = updated.save.maps.get(run.resourceId)
        if dungeon:
            dungeon.current = marker.checkpoint if next_run.hp else "@entrance"
    if next_run.phase == "cleared":
        updated.save.clears[run.resourceId] = (
            updated.save.clears.get(run.resourceId, 0) + 1
        )
    if next_run.phase == "battle":
        updated.save.battle.enemies = survivors
        updated.save.battle.turn += 1
        # Starts only on explicit next-turn acknowledgement, not while reading results.
        next_run.answerDeadline = None
    else:
        updated.save.battle = None
        next_run.answerDeadline = None
    summary = (
        f"正解 {sum(results.values())}/{len(marker.enemies)} · "
        f"撃破 {len(set(body.defeated))}体 · HP -{damage}"
    )
    if body.retreat:
        summary += " · 撤退"
    updated.save.battleFeedback = summary
    return updated


async def abandon_combat(user_id: UUIDy) -> GameState:
    """ブラウザ内の戦闘を失ったら未回答として一度だけ撤退する."""
    state = await read_state(user_id)
    marker = state.save.battle
    if not marker and state.save.run and state.save.run.phase == "battle":
        # Deployed single-enemy snapshots cannot resume as the new battle protocol.
        updated = state.model_copy(deep=True)
        run = updated.save.run
        balance = await get_game_balance()
        stats = updated.save.allocation.stats(balance)
        damage = (
            0
            if state.save.battleFeedback
            else max(1, balance.enemy_attack - stats["defense"])
        )
        for key, value in stats.items():
            setattr(run, key, value)
        run.hp = max(0, min(run.hp, run.maxHp) - damage)
        run.phase = (
            "defeated"
            if not run.hp
            else "rest"
            if run.moves >= MOVES_PER_EVENT
            else "path"
        )
        run.enemyHp = 0
        run.answerDeadline = None
        dungeon = updated.save.maps.get(run.resourceId)
        if dungeon:
            previous_place = run.readIds[-2] if len(run.readIds) > 1 else "@entrance"
            dungeon.current = (
                previous_place
                if run.hp and previous_place in {p.id for p in dungeon.places}
                else "@entrance"
            )
        updated.save.battleFeedback = (
            "旧戦闘から撤退しました。保存済みの回答・XPは保持しています。"
        )
        async with adb.transaction:
            await persist(user_id, state, updated)
        return updated
    if marker:
        # A fully saved result screen has no unanswered current turn.
        if state.save.battleFeedback:
            updated = state.model_copy(deep=True)
            updated.save.battle = None
            updated.save.run.phase = (
                "rest" if updated.save.run.moves >= MOVES_PER_EVENT else "path"
            )
            dungeon = updated.save.maps.get(updated.save.run.resourceId)
            if dungeon:
                dungeon.current = marker.checkpoint
            async with adb.transaction:
                await persist(user_id, state, updated)
            return updated
        result = await settle_turn(
            user_id,
            TurnRequest(battle_id=marker.id, turn=marker.turn, retreat=True),
        )
        return GameState.model_validate(result["state"])
    return state


async def next_turn(user_id: UUIDy, body: TurnRequest) -> GameState:
    """結果確認後に共通の持ち時間を開始する."""
    previous = await read_state(user_id)
    marker = previous.save.battle
    if (
        not marker
        or (marker.id, marker.turn) != (body.battle_id, body.turn)
        or not previous.save.battleFeedback
    ):
        raise HTTPException(409, "次のターンを開始できません。")
    updated = previous.model_copy(deep=True)
    seconds = previous.save.allocation.stats(await get_game_balance())["answerSeconds"]
    updated.save.run.answerSeconds = seconds
    updated.save.run.answerDeadline = int(time() * 1000) + seconds * 1000
    updated.save.battleFeedback = None
    async with adb.transaction:
        await persist(user_id, previous, updated)
    return updated


async def set_allocation(user_id: UUIDy, allocation: Allocation) -> GameState:
    """自由に振り直せるが戦闘中は禁止。振り直しだけではHPを回復しない."""
    previous = await read_state(user_id)
    if previous.save.battle or (
        previous.save.run and previous.save.run.phase == "battle"
    ):
        raise HTTPException(409, "戦闘中はステータスを変更できません。")
    budget = ((await fetch_learning_progress(user_id)).level - 1) * 3
    if sum(allocation.model_dump().values()) > budget:
        raise HTTPException(422, "育成ポイントが不足しています。")
    updated = previous.model_copy(deep=True)
    updated.save.allocation = allocation
    stats = allocation.stats(await get_game_balance())
    for run in [updated.save.run, *(d.run for d in updated.save.dungeons.values())]:
        if run:
            for key, value in stats.items():
                setattr(run, key, value)
            run.hp = min(run.hp, run.maxHp)
    async with adb.transaction:
        await persist(user_id, previous, updated)
    return updated
