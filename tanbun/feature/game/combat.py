"""操作はブラウザ、戦闘の開始・精算・撤退はrevision付きで保存する."""

import json
from collections.abc import Callable
from secrets import choice, randbelow
from time import time
from uuid import uuid4

from fastapi import HTTPException
from neomodel import adb

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.gamification.usecase import fetch_learning_progress
from tanbun.feature.notification.usecase import dispatch_saved_notifications
from tanbun.feature.quiz.answering.repo import create_answer_in_transaction
from tanbun.feature.quiz.chain.usecase import expand_quiz_chain

from .balance import Allocation, GameBalance, get_game_balance
from .catalog import get_game_catalog
from .combat_models import CombatContext, EncounterRequest, EnemyStats, TurnRequest
from .combat_resolution import (
    ENEMIES_TO_CLEAR,
    MOVES_PER_EVENT,
    apply_combat_stats,
    apply_on_hit_effects,
    apply_turn_result,
    award_kill_gold,
    record_boss_victory,
    status_feedback,
    wear_defensive_equipment,
)
from .damage import enemy_damage
from .effects import (
    curse_damage,
    modified_stat,
    poison_damage,
    sync_bubble,
)
from .enemy_stats import derive_boss, derive_enemies, label_enemies, style_enemies
from .gambits import prepare_action
from .inventory import (
    equipped_stats,
)
from .rewards import GoldSettings
from .state import BattleMarker, GameState, StateUpdate, compare_and_save, read_state

__all__ = [
    "ENEMIES_TO_CLEAR",
    "MOVES_PER_EVENT",
    "CombatContext",
    "EncounterRequest",
    "EnemyStats",
    "TurnRequest",
    "abandon_combat",
    "apply_combat_stats",
    "apply_on_hit_effects",
    "apply_turn_result",
    "award_kill_gold",
    "combat_context",
    "derive_boss",
    "derive_enemies",
    "evaluate_answers",
    "label_enemies",
    "next_turn",
    "persist",
    "read_receipt",
    "record_boss_victory",
    "select_encounter",
    "set_allocation",
    "settle_turn",
    "start_combat",
    "status_feedback",
    "style_enemies",
    "wear_defensive_equipment",
]


async def combat_context(
    user_id: UUIDy,
    state: GameState | None = None,
    *,
    balance: GameBalance | None = None,
) -> CombatContext:
    """固定された全領域の敵をまとめて評価。イベントや秒ごとの問い合わせはしない."""
    state = state or await read_state(user_id)
    balance = balance or await get_game_balance()
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
    battle_payload = state.save.battle.model_dump() if state.save.battle else {}
    from .skill_outcomes import normal_attack  # noqa: PLC0415

    catalog = await get_game_catalog()
    return CombatContext(
        balance=balance,
        gold=catalog.rewards.gold if catalog.rewards else GoldSettings(),
        resource_power=power,
        achievement=growth[0].level if growth else 0,
        default_attack=normal_attack(catalog),
        enabled_elements=[
            element.id for element in catalog.elements if element.enabled
        ],
        elements=catalog.elements,
        enemies=label_enemies(
            style_enemies(
                derive_enemies(
                    {**content, "battle": battle_payload},
                    relations,
                    power,
                    balance,
                ),
                catalog,
            ),
            state.save.battle,
        ),
    )


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
        *dungeon.chest_ids,
    }:
        raise HTTPException(422, "撤退先がマップにありません。")
    last_place = dungeon.places[-1] if dungeon and dungeon.places else None
    if body.discovered_place is not None and (
        not last_place
        or (dungeon.current, last_place.id, last_place.region)
        != (body.discovered_place, body.discovered_place, body.region)
        or body.discovered_place == body.checkpoint
    ):
        raise HTTPException(422, "新しく開拓した地点がマップと一致しません。")
    context = await combat_context(user_id, previous)
    unique = {
        enemy.id: enemy for enemy in context.enemies if enemy.region == body.region
    }
    if not unique:
        raise HTTPException(
            422,
            "有効な戦闘クイズがありません。候補を更新してください。",
        )
    chosen = select_encounter(list(unique.values()), context.balance, body.region)
    updated = previous.model_copy(deep=True)
    updated.save.battle = BattleMarker(
        id=uuid4().hex,
        region=body.region,
        checkpoint=body.checkpoint,
        discoveredPlace=body.discovered_place,
        enemies=[e.id for e in chosen],
        enemyLabels={enemy.id: chr(65 + index) for index, enemy in enumerate(chosen)},
        quizIndices={
            enemy.id: choice(enemy.quizIndexes or [enemy.quizIndex]) for enemy in chosen
        },
    )
    chosen = label_enemies(chosen, updated.save.battle)
    context.enemies = label_enemies(context.enemies, updated.save.battle)
    stats = equipped_stats(
        updated.save.allocation,
        context.balance,
        updated.save.economy,
    )
    next_run = updated.save.run
    for key, value in stats.items():
        if key not in {"maxHp", "maxMp"}:
            setattr(next_run, key, value)
    sync_bubble(next_run, updated.save.battle, stats["maxHp"], stats["maxMp"])
    next_run.hp = min(next_run.hp, next_run.maxHp)
    next_run.mp = stats["maxMp"]
    next_run.enemyHp = 0
    next_run.enemyMaxHp = 1
    next_run.answerDeadline = int(time() * 1000) + stats["answerSeconds"] * 1000
    await prepare_action(updated.save, user_id)
    if body.scheduled:
        from .scheduled_combat import (  # noqa: PLC0415
            initialize_schedule,
            prepare_live_gambit,
        )

        initialize_schedule(updated, context.model_copy(update={"enemies": chosen}))
        await prepare_live_gambit(user_id, updated)
    async with atomic():
        await persist(user_id, previous, updated)
    return updated


def select_encounter(
    enemies: list[EnemyStats],
    balance: GameBalance,
    region: int,
    *,
    pick: Callable | None = None,
    draw: Callable | None = None,
) -> list[EnemyStats]:
    """Live and practice battles share count/type/instance selection."""
    lower, upper = balance.encounter_range(len(enemies), region)
    if not upper:
        return []
    pick, draw = pick or choice, draw or randbelow
    count = lower + draw(upper - lower + 1)
    occurrences: dict[str, int] = {}
    chosen = []
    for _ in range(count):
        enemy = pick(enemies)
        ordinal = occurrences.get(enemy.id, 0)
        occurrences[enemy.id] = ordinal + 1
        chosen.append(
            enemy.model_copy(
                update={
                    "id": enemy.id if ordinal == 0 else f"{enemy.id}#{ordinal}",
                },
            ),
        )
    return chosen


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
    if marker.schedule:
        from .scheduled_combat import settle_scheduled  # noqa: PLC0415

        return await settle_scheduled(user_id, previous, body)
    if not set(body.answers) <= set(marker.enemies) or not set(body.defeated) <= set(
        marker.enemies,
    ):
        raise HTTPException(422, "この戦闘にいない敵です。")
    context = await combat_context(user_id, previous)
    enemies = {enemy.id: enemy for enemy in context.enemies}
    results, selected_quizzes = await evaluate_answers(user_id, previous, body, enemies)
    if any(not results.get(enemy_id) for enemy_id in body.defeated):
        raise HTTPException(422, "未正解の敵は撃破できません。")
    stats = equipped_stats(
        previous.save.allocation,
        context.balance,
        previous.save.economy,
    )
    incoming_damage = {
        e: enemy_damage(
            modified_stat(
                enemies[e].attack if e in enemies else context.balance.enemy_attack,
                marker,
                e,
                "attack",
            ),
            modified_stat(stats["defense"], marker, "player", "defense"),
            context.balance,
        )
        for e in marker.enemies
        if not results.get(e)
    }
    damage = sum(incoming_damage.values())
    updated = apply_turn_result(
        previous,
        body,
        results,
        damage,
        stats,
        on_hit_skills={
            e: enemy.on_hit_skill for e, enemy in enemies.items() if enemy.on_hit_skill
        },
        base_stats=previous.save.allocation.stats(context.balance),
        context=context,
    )
    award_kill_gold(updated, body, context=context)
    receipt = {
        "id": f"{marker.id}:{marker.turn}",
        "results": results,
        "damage": damage,
        "incoming_damage": incoming_damage,
        "player_poison_damage": min(
            run.hp,
            poison_damage(marker, {"player": run.maxHp}).get("player", 0),
        ),
        "curse_damage": min(
            run.mp,
            curse_damage(marker, {"player": run.maxMp}).get("player", 0),
        ),
        "poison_damage": poison_damage(
            marker,
            {e: enemies[e].hp for e in marker.enemies if e in enemies},
        ),
        "gold_earned": updated.save.economy.gold - previous.save.economy.gold,
    }
    notifications = []
    async with atomic():
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


async def abandon_combat(user_id: UUIDy) -> GameState:
    """ブラウザ内の戦闘を失ったら未回答として一度だけ撤退する."""
    state = await read_state(user_id)
    marker = state.save.battle
    if marker and marker.endAction:
        from .end_actions import EndActionAnswer, answer_end_action  # noqa: PLC0415

        while state.save.battle and state.save.battle.endAction:
            action = state.save.battle.endAction
            result = await answer_end_action(
                user_id,
                EndActionAnswer(
                    action_id=action["id"],
                    index=action["index"],
                    selected=None,
                ),
            )
            state = result["state"]
        marker = state.save.battle
    if not marker and state.save.run and state.save.run.phase == "battle":
        # Deployed single-enemy snapshots cannot resume as the new battle protocol.
        updated = state.model_copy(deep=True)
        run = updated.save.run
        balance = await get_game_balance()
        stats = equipped_stats(updated.save.allocation, balance, updated.save.economy)
        damage = (
            0
            if state.save.battleFeedback
            else enemy_damage(balance.enemy_attack, stats["defense"], balance)
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
        async with atomic():
            await persist(user_id, state, updated)
        return updated
    if marker:
        if marker.skill:
            from .skills import SkillAnswer, resolve_skill  # noqa: PLC0415

            result = await resolve_skill(
                user_id,
                SkillAnswer(skill_id=marker.skill.id, retreat=True),
            )
            return result["state"]
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
            async with atomic():
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
        marker
        and marker.schedule
        and (marker.id, marker.turn) == (body.battle_id, body.turn)
        and marker.schedule.actor_id is not None
        and not previous.save.battleFeedback
    ):
        return previous
    if (
        not marker
        or (marker.id, marker.turn) != (body.battle_id, body.turn)
        or not previous.save.battleFeedback
        or marker.skill
        or marker.endAction
    ):
        raise HTTPException(409, "次のターンを開始できません。")
    updated = previous.model_copy(deep=True)
    context = await combat_context(user_id, previous)
    if marker.schedule:
        from .scheduled_combat import (  # noqa: PLC0415
            begin_scheduled_action,
            prepare_live_gambit,
        )

        begin_scheduled_action(updated, context)
        await prepare_live_gambit(user_id, updated)
        async with atomic():
            await persist(user_id, previous, updated)
        return updated
    selected = {
        enemy.id: choice(enemy.quizIndexes or [enemy.quizIndex])
        for enemy in context.enemies
        if enemy.id in marker.enemies
    }
    updated.save.battle.quizIndices = selected
    updated.save.battle.skillUsed = False
    seconds = previous.save.allocation.stats(await get_game_balance())["answerSeconds"]
    updated.save.run.answerSeconds = seconds
    updated.save.run.answerDeadline = int(time() * 1000) + seconds * 1000
    updated.save.battleFeedback = None
    updated.save.quizFeedback = None
    updated.save.battleActionDetails = None
    await prepare_action(updated.save, user_id)
    async with atomic():
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
    stats = equipped_stats(allocation, await get_game_balance(), updated.save.economy)
    for run in [updated.save.run, *(d.run for d in updated.save.dungeons.values())]:
        if run:
            for key, value in stats.items():
                setattr(run, key, value)
            run.hp = min(run.hp, run.maxHp)
            run.mp = min(run.mp, run.maxMp)
    async with atomic():
        await persist(user_id, previous, updated)
    return updated
