"""Administrative practice sessions: read sources, never persist any outcome."""

from random import Random
from time import time
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from tanbun.feature.domain.types import to_uuid
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.quiz.chain.usecase import expand_quiz_chain
from tanbun.feature.quiz.domain.domain import ReadableQuiz

from .admin import EnemyBalanceSimulationResult, _dungeon_content, _legacy_pool_ids
from .balance import Allocation, GameBalance
from .catalog import GameCatalog, StoredSkill, default_catalog, get_game_catalog
from .combat import (
    CombatContext,
    EnemyStats,
    TurnRequest,
    apply_turn_result,
    combat_context,
    select_encounter,
    style_enemies,
)
from .damage import damage_range
from .effects import curse_damage, modified_stat, poison_damage
from .end_actions import (
    EndActionAnswer,
    EndActionRequest,
    begin_action,
    finish_action,
    prepare_counter_damage,
)
from .gambit_slots import gambit_capacity
from .gambits import choose_action
from .inventory import Economy, equipped_stats
from .roster import assign_enemy_quizzes, enemy_identity
from .skill_outcomes import has_counter
from .skills import specialty_pools
from .specialties import strengthened
from .state import BattleMarker, GameSave, GameState, Run, read_state


class PracticeRequest(BaseModel):
    """Draft settings and existing dungeon content; no generation or writes."""

    user_id: UUID
    scheduled: bool = False
    player_user_id: UUID | None = None
    resource_id: UUID
    level: int = Field(ge=1, le=20)
    balance: GameBalance
    allocation: Allocation | None = None
    seed: int = Field(default=1, ge=0, le=2**31 - 1)


class PracticeSession(BaseModel):
    """Only the browser owns this state; requests are stateless calculators."""

    state: GameState
    context: CombatContext
    seed: int = Field(ge=0, le=2**31 - 1)
    server_now: int
    catalog: GameCatalog = Field(default_factory=default_catalog)
    gambit_slots: int = Field(default=1, ge=1, le=10)
    specialty_quizzes: dict[str, list[ReadableQuiz]] = Field(default_factory=dict)
    specialty_skills: dict[str, StoredSkill] = Field(default_factory=dict)


class SimulatedPracticeRequest(BaseModel):
    """Use an enemy trial snapshot and the current draft player/damage settings."""

    simulation: EnemyBalanceSimulationResult
    scheduled: bool = False
    balance: GameBalance
    player_user_id: UUID | None = None
    allocation: Allocation | None = None
    seed: int = Field(default=1, ge=0, le=2**31 - 1)


class PracticeTurn(BaseModel):
    """A sealed practice turn, isolated from live battle endpoints."""

    session: PracticeSession
    turn: TurnRequest


def _random(seed: int, turn: int = -1) -> Random:
    return Random(f"practice:{seed}:{turn}")  # noqa: S311 - reproducible simulation


async def prepare_practice(body: PracticeRequest) -> PracticeSession:
    """Read a saved population and rebuild only the disposable simulation copy."""
    source = await read_state(body.user_id)
    content = _dungeon_content(source, body.resource_id.hex)
    if not content:
        raise HTTPException(
            409,
            "保存済みのダンジョンがありません。まず冒険してください。",
        )
    region = body.level - 1
    pool = (content.get("regionQuizPools") or {}).get(str(region))
    if not pool:
        pool = _legacy_pool_ids(
            (content.get("regionEnemies") or {}).get(str(region), []),
            content.get("quizzes", []),
        )
    if not pool:
        raise HTTPException(
            409,
            "この領域の母集団がありません。準備済み領域を選んでください。",
        )
    source = source.model_copy(deep=True)
    content = _dungeon_content(source, body.resource_id.hex)
    pool_ids = {str(uid).replace("-", "").lower() for uid in pool}
    rng = _random(body.seed)
    content = _practice_content(content, body, pool_ids, rng)
    run = source.save.run
    if not run or run.resourceId.replace("-", "").lower() != body.resource_id.hex:
        run = next(
            dungeon.run
            for key, dungeon in source.save.dungeons.items()
            if key.replace("-", "").lower() == body.resource_id.hex
        )
    player_source = source
    if body.player_user_id is not None and body.player_user_id != body.user_id:
        player_source = await read_state(body.player_user_id)
    allocation = (body.allocation or player_source.save.allocation).model_copy(
        deep=True,
    )
    economy = player_source.save.economy.model_copy(deep=True)
    stats = equipped_stats(allocation, body.balance, economy)
    run = run.model_copy(
        update={
            **stats,
            "hp": stats["maxHp"],
            "mp": stats["maxMp"],
            "moves": 0,
            "kills": 0,
            "quizCursor": 0,
            "readIds": [],
            "phase": "battle",
        },
    )
    state = GameState(
        save=GameSave(
            run=run,
            allocation=allocation,
            economy=economy,
            content=content,
            gambits=player_source.save.gambits,
            bossClears=player_source.save.bossClears,
            specialties=player_source.save.specialties,
        ),
    )
    context = await combat_context(body.user_id, state, balance=body.balance)
    prepared = _begin_practice(
        state,
        context,
        region,
        body.seed,
        rng,
        catalog=await get_game_catalog(),
        slots=(
            await gambit_capacity(body.player_user_id or body.user_id, body.balance)
        ).slots,
    )
    if body.scheduled:
        from .scheduled_combat import (  # noqa: PLC0415
            initialize_schedule,
            prepare_scheduled_gambit,
        )

        initialize_schedule(prepared.state, prepared.context)
        prepare_scheduled_gambit(
            prepared.state,
            prepared.catalog,
            prepared.gambit_slots,
        )
    return await _prepare_specialties(prepared, body.player_user_id or body.user_id)


def _practice_content(
    content: dict,
    body: PracticeRequest,
    pool_ids: set[str],
    rng: Random,
) -> dict:
    """Keep saved enemy sets while carrying only this region's readable quizzes."""
    region = body.level - 1
    old_quizzes = content.get("quizzes", [])
    retained = [
        index
        for index, quiz in enumerate(old_quizzes)
        if str(quiz.get("quiz_id", "")).replace("-", "").lower() in pool_ids
    ]
    remap = {old: new for new, old in enumerate(retained)}
    stored_enemies = (content.get("regionEnemies") or {}).get(str(region), [])
    content = {
        "knowledge": [],
        "quizzes": [old_quizzes[index] for index in retained],
    }
    if stored_enemies:
        roster = []
        for enemy in stored_enemies:
            indexes = [
                remap[index]
                for index in enemy.get("quizIndexes", [enemy.get("quizIndex")])
                if index in remap
            ]
            if indexes:
                roster.append({
                    **enemy,
                    "quizIndex": indexes[0],
                    "quizIndexes": indexes,
                })
    else:
        groups = assign_enemy_quizzes(
            list(range(len(content["quizzes"]))),
            body.balance,
            region,
            rng=rng,
        )
        roster = [
            {
                "id": enemy_identity(body.resource_id.hex, region, i),
                "name": f"領域 {body.level}の敵 {i + 1}",
                "quizIndex": group[0],
                "quizIndexes": group,
            }
            for i, group in enumerate(groups)
        ]
    content["regionEnemies"] = {str(region): roster}
    return content


async def prepare_simulated_practice(body: SimulatedPracticeRequest) -> PracticeSession:
    """Keep trial enemy stats exactly; create no user state or real quizzes."""
    trial = body.simulation
    region = trial.achievement - 1
    quiz_count = max((enemy.quiz_count for enemy in trial.enemies), default=0)
    quizzes = [
        {
            "quiz_id": str(uuid4()),
            "quiz_type": "sent2term",
            "prompt": {"subject": "模擬回答", "answer_kind": "term"},
            "statement": "正解か不正解を選んでください。",
            "options": {"yes": "正解", "no": "不正解"},
            "correct": ["yes"],
            "no_correct_option": False,
            "created": "2026-01-01T00:00:00Z",
        }
        for _ in range(quiz_count)
    ]
    player_source = (
        await read_state(body.player_user_id)
        if body.player_user_id is not None
        else None
    )
    economy = (
        player_source.save.economy.model_copy(deep=True) if player_source else Economy()
    )
    allocation = body.allocation
    if allocation is None:
        allocation = (
            player_source.save.allocation if player_source is not None else Allocation()
        )
    allocation = allocation.model_copy(deep=True)
    stats = equipped_stats(allocation, body.balance, economy)
    state = GameState(
        save=GameSave(
            allocation=allocation,
            economy=economy,
            gambits=player_source.save.gambits if player_source else [],
            bossClears=player_source.save.bossClears if player_source else {},
            specialties=player_source.save.specialties if player_source else [],
            content={"knowledge": [], "quizzes": quizzes},
            run=Run(
                resourceId="simulation",
                name=f"Power {trial.power} · 領域 {trial.achievement}",
                **stats,
                hp=stats["maxHp"],
                mp=stats["maxMp"],
                moves=0,
                kills=0,
                enemyHp=0,
                enemyMaxHp=1,
                quizCursor=0,
                readIds=[],
                phase="battle",
            ),
        ),
    )
    context = CombatContext(
        balance=body.balance,
        enemies=[
            EnemyStats(
                id=enemy_identity("simulation", region, enemy.index - 1),
                name=f"領域 {trial.achievement}の敵 {enemy.index}",
                quizIndex=0,
                quizIndexes=list(range(enemy.quiz_count)),
                hp=enemy.hp,
                attack=enemy.attack,
                mp=enemy.mp,
                special_attack=enemy.special_attack,
                special_defense=enemy.special_defense,
                speed=enemy.speed,
                relations=enemy.relations,
                region=region,
            )
            for enemy in trial.enemies
            if enemy.quiz_count > 0
        ],
    )
    catalog = await get_game_catalog()
    context.enemies = style_enemies(context.enemies, catalog, preserve_stats=True)
    prepared = _begin_practice(
        state,
        context,
        region,
        body.seed,
        _random(body.seed),
        catalog=catalog,
        slots=(await gambit_capacity(body.player_user_id, body.balance)).slots
        if body.player_user_id
        else 1,
    )
    if body.scheduled:
        from .scheduled_combat import (  # noqa: PLC0415
            initialize_schedule,
            prepare_scheduled_gambit,
        )

        initialize_schedule(prepared.state, prepared.context)
        prepare_scheduled_gambit(prepared.state, catalog, prepared.gambit_slots)
    return await _prepare_specialties(prepared, body.player_user_id)


def _begin_practice(
    state: GameState,
    context: CombatContext,
    region: int,
    seed: int,
    rng: Random,
    *,
    catalog: GameCatalog,
    slots: int = 1,
) -> PracticeSession:
    """Share encounter draws and turn initialization for both trial sources."""
    from .skill_outcomes import normal_attack  # noqa: PLC0415

    context.default_attack = normal_attack(catalog)
    chosen = select_encounter(
        context.enemies,
        context.balance,
        region,
        pick=rng.choice,
        draw=rng.randrange,
    )
    if not chosen:
        raise HTTPException(409, "有効なクイズがありません。母集団を更新してください。")
    context.enemies = chosen
    state.save.battle = BattleMarker(
        id=f"practice-{seed}",
        region=region,
        enemies=[enemy.id for enemy in chosen],
        quizIndices={enemy.id: rng.choice(enemy.quizIndexes) for enemy in chosen},
    )
    now = int(time() * 1000)
    stats = equipped_stats(state.save.allocation, context.balance, state.save.economy)
    state.save.run.answerDeadline = now + stats["answerSeconds"] * 1000
    state.save.battle.gambitAction = None
    return PracticeSession(
        state=state,
        context=context,
        seed=seed,
        server_now=now,
        catalog=catalog,
        gambit_slots=slots,
    )


def validate_practice_questions(marker, turn, enemies, quizzes) -> None:
    """Restrict scheduled battles to their single sealed question."""
    if not set(turn.answers) <= set(marker.enemies) or not set(turn.defeated) <= set(
        marker.enemies,
    ):
        raise HTTPException(422, "模擬戦に存在しない敵です。")
    if marker.schedule and set(turn.answers) - {marker.schedule.quiz_enemy_id}:
        raise HTTPException(422, "現在の行動以外には回答できません。")
    if any(
        enemy not in enemies
        or enemy not in marker.quizIndices
        or not 0 <= marker.quizIndices[enemy] < len(quizzes)
        for enemy in (
            [marker.schedule.quiz_enemy_id] if marker.schedule else marker.enemies
        )
    ):
        raise HTTPException(422, "模擬戦の敵またはクイズが見つかりません。")


def practice_turn(body: PracticeTurn) -> dict:
    """Reuse correctness, damage and turn transitions without saving answers/XP."""
    session, turn = body.session, body.turn
    state, marker = session.state, session.state.save.battle
    if (
        not marker
        or not state.save.run
        or (marker.id, marker.turn) != (turn.battle_id, turn.turn)
    ):
        raise HTTPException(422, "模擬戦のターンが一致しません。")
    enemies = {enemy.id: enemy for enemy in session.context.enemies}
    quizzes = (state.save.content or {}).get("quizzes", [])
    validate_practice_questions(marker, turn, enemies, quizzes)
    results = {}
    for enemy_id, selected in turn.answers.items():
        try:
            quiz = ReadableQuiz.model_validate(quizzes[marker.quizIndices[enemy_id]])
        except ValidationError as error:
            raise HTTPException(
                422,
                "模擬戦のクイズを読み取れません。再開してください。",
            ) from error
        if not set(selected) <= set(quiz.options):
            raise HTTPException(422, "クイズに存在しない選択肢です。")
        results[enemy_id] = quiz.is_correct(selected)
    if any(not results.get(enemy) for enemy in turn.defeated):
        raise HTTPException(422, "未正解の敵は撃破できません。")
    if marker.schedule:
        return scheduled_practice_turn(session, turn, results)
    stats = equipped_stats(
        state.save.allocation,
        session.context.balance,
        state.save.economy,
    )
    rng = _random(session.seed, marker.turn)
    incoming_damage = {}
    for enemy in marker.enemies:
        if not results.get(enemy):
            low, _, high = damage_range(
                modified_stat(enemies[enemy].attack, marker, enemy, "attack"),
                modified_stat(stats["defense"], marker, "player", "defense"),
                session.context.balance,
            )
            incoming_damage[enemy] = rng.randint(low, high)
    damage = sum(incoming_damage.values())
    return {
        "state": apply_turn_result(
            state,
            turn,
            results,
            damage,
            stats,
            on_hit_skills={
                e: enemy.on_hit_skill
                for e, enemy in enemies.items()
                if enemy.on_hit_skill
            },
        ),
        "results": results,
        "damage": damage,
        "incoming_damage": incoming_damage,
        "player_poison_damage": min(
            state.save.run.hp,
            poison_damage(marker, {"player": state.save.run.maxHp}).get("player", 0),
        ),
        "curse_damage": min(
            state.save.run.mp,
            curse_damage(marker, {"player": state.save.run.maxMp}).get("player", 0),
        ),
        "poison_damage": poison_damage(
            marker,
            {e: enemies[e].hp for e in marker.enemies},
        ),
    }


def scheduled_practice_turn(session, turn, results):
    """Use deterministic damage but the same scheduler and resolver as live."""
    from .scheduled_combat import resolve_scheduled  # noqa: PLC0415

    state = session.state
    rng = _random(session.seed, state.save.battle.turn)

    def roll(attack, defense, balance):
        low, _, high = damage_range(attack, defense, balance)
        return rng.randint(low, high)

    if (
        state.save.run.answerDeadline
        and int(time() * 1000) > state.save.run.answerDeadline
    ):
        turn = turn.model_copy(update={"answers": {}})
        results = {}
    return resolve_scheduled(
        state,
        turn,
        results,
        session.context,
        roll_player=lambda attack, balance: roll(attack, 0, balance),
        roll_enemy=roll,
    )


def next_practice_turn(session: PracticeSession) -> GameState:
    """Start the next disposable turn only after reading its feedback."""
    state = session.state.model_copy(deep=True)
    marker = state.save.battle
    if not marker or not state.save.run or not state.save.battleFeedback:
        raise HTTPException(422, "模擬戦の次のターンを開始できません。")
    if marker.endAction:
        raise HTTPException(422, "専門分野の回答を完了してください。")
    if marker.schedule:
        from .scheduled_combat import (  # noqa: PLC0415
            begin_scheduled_action,
            prepare_scheduled_gambit,
        )

        begin_scheduled_action(state, session.context)
        prepare_scheduled_gambit(state, session.catalog, session.gambit_slots)
        return state
    rng = _random(session.seed, marker.turn)
    enemies = {enemy.id: enemy for enemy in session.context.enemies}
    if not set(marker.enemies) <= enemies.keys():
        raise HTTPException(422, "模擬戦の敵が見つかりません。")
    marker.quizIndices = {
        enemy: rng.choice(enemies[enemy].quizIndexes) for enemy in marker.enemies
    }
    state.save.battleFeedback = None
    marker.effects = [e for e in marker.effects if e.expires_turn > marker.turn]
    marker.gambitAction = None
    state.save.run.answerDeadline = (
        int(time() * 1000)
        + session.state.save.allocation.stats(session.context.balance)["answerSeconds"]
        * 1000
    )
    return state


async def _prepare_specialties(session: PracticeSession, user_id) -> PracticeSession:
    """実プレイヤーの専門分野の出題元を読み取るだけ。回答やXPは保存しない."""
    if not user_id or not session.state.save.specialties:
        return session
    resources = {
        to_uuid(r.resource_id).hex: r for r in await fetch_resource_growth(user_id)
    }
    skills = {s.id: s for s in session.catalog.skills}
    for specialty in session.state.save.specialties:
        if specialty.skill_id in skills:
            session.specialty_skills[specialty.id] = strengthened(
                skills[specialty.skill_id],
                specialty,
                resources,
                next(
                    (
                        u.power_coefficient
                        for u in session.catalog.specialty_skills
                        if u.skill_id == specialty.skill_id
                    ),
                    1,
                ),
            )
    for pool in await specialty_pools(user_id, session.state):
        values = []
        for quiz_id in pool["quiz_ids"]:
            chain = await expand_quiz_chain(user_id, to_uuid(quiz_id))
            values.append(chain.quizzes[0].readable)
        session.specialty_quizzes[pool["resource_id"]] = values
    return session


class PracticeAction(BaseModel):
    """使い捨てガンビット精算。実戦APIに転送しない."""

    session: PracticeSession
    answer: bool = False
    body: dict


def _practice_candidates(session: PracticeSession, save: GameSave) -> GameSave:
    """出題元が不足する専門分野だけを候補から外し、所有状態は変えない."""
    search = save.model_copy(deep=True)
    for specialty in search.specialties:
        use = next(
            (
                u
                for u in session.catalog.specialty_skills
                if u.skill_id == specialty.skill_id and u.enabled
            ),
            None,
        )
        if (
            use
            and use.quiz_required
            and any(
                not session.specialty_quizzes.get(r) for r in specialty.resource_ids
            )
        ):
            specialty.equipped = False
    return search


def practice_action(body: PracticeAction) -> dict:
    """通常回答から独立したターン末行動を実戦と同じ効果処理で試す."""
    session = body.session
    state = session.state.model_copy(deep=True)
    marker = state.save.battle
    if not marker or not state.save.battleFeedback:
        raise HTTPException(422, "模擬戦の結果処理後に実行してください。")
    quizzes = {
        str(q.quiz_id): q
        for values in session.specialty_quizzes.values()
        for q in values
    }
    outgoing = {}
    if body.answer:
        outgoing = _answer_practice_action(
            state,
            EndActionAnswer.model_validate(body.body),
            quizzes,
        )
    elif not marker.endActionDone:
        request = EndActionRequest.model_validate(body.body)
        if (marker.id, marker.turn) != (request.battle_id, request.turn):
            raise HTTPException(422, "模擬ターンが変わっています。")
        marker.endActionDone = True
        state.save.endActionReceipt = None
        selected = choose_action(
            _practice_candidates(session, state.save),
            session.catalog,
            slots=session.gambit_slots,
        )
        if selected:
            skill = session.specialty_skills.get(selected.specialty_id, selected.skill)
            ids = []
            if selected.specialty_id and selected.specialty_use.quiz_required:
                specialty = next(
                    s for s in state.save.specialties if s.id == selected.specialty_id
                )
                if any(
                    not session.specialty_quizzes.get(r) for r in specialty.resource_ids
                ):
                    raise HTTPException(422, "模擬専門分野の出題元が不足しています。")
                rng = _random(session.seed, marker.turn)
                resource = rng.choice(specialty.resource_ids)
                ids = [str(rng.choice(session.specialty_quizzes[resource]).quiz_id)]
            begin_action(
                state.save,
                selected,
                skill,
                ids,
                marker.schedule.enemy_hp if marker.schedule else request.enemy_hp,
                counter_damage=prepare_counter_damage(state, session.context)
                if ids and has_counter(skill)
                else 0,
            )
            if not ids:
                outgoing = finish_action(state)
    action = state.save.battle.endAction if state.save.battle else None
    return {
        "state": state,
        "action": action,
        "quiz": quizzes[action["quiz_ids"][action["index"]]] if action else None,
        "outgoing_damage": outgoing,
        "animation": (state.save.endActionReceipt or {}).get("animation", "none")
        if not action
        else "none",
    }


def _answer_practice_action(state, answer: EndActionAnswer, quizzes: dict) -> dict:
    """模擬回答の検査と一問分の進行。実際の回答履歴には触れない."""
    action = state.save.battle.endAction
    if not action or (action["id"], action["index"]) != (
        answer.action_id,
        answer.index,
    ):
        raise HTTPException(422, "模擬専用クイズの回答順が変わっています。")
    quiz = quizzes[action["quiz_ids"][action["index"]]]
    if answer.selected is not None and not set(answer.selected) <= set(quiz.options):
        raise HTTPException(422, "選択肢がありません。")
    action["correct"] += int(
        answer.selected is not None
        and int(time() * 1000) <= action["deadline"]
        and quiz.is_correct(answer.selected),
    )
    action["index"] += 1
    action["deadline"] = (
        int(time() * 1000)
        + action.get("quiz_seconds", action["skill"].get("quiz_seconds", 30)) * 1000
    )
    return finish_action(state) if action["index"] == len(action["quiz_ids"]) else {}
