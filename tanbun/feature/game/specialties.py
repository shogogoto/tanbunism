"""専門分野の作成・装備・配合。取得元は一度だけ使い、最新Powerを導出する."""

from math import floor, log1p
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from tanbun.config.transactions import atomic
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.gamification.resource import fetch_resource_growth
from tanbun.feature.gamification.usecase import fetch_learning_progress

from .balance import get_game_balance
from .catalog import get_game_catalog
from .combat import persist
from .specialty_types import Specialty
from .state import read_state

FUSION_PARTS = 2


class SpecialtyUpdate(BaseModel):
    """操作ごとの入力。更新番号で別端末からの配合の二重実行を防ぐ."""

    revision: int = Field(ge=0)
    operation: str
    resource_id: UUID | None = None
    skill_id: str | None = None
    ids: list[str] = Field(default_factory=list, max_length=2)
    equipped: bool = False


async def specialty_capacity(user_id) -> dict:
    """装備数だけを制限し、未装備の所持数を制限しない."""
    level = (await fetch_learning_progress(user_id)).level
    interval = (await get_game_balance()).specialty_levels_per_slot
    return {
        "slots": 1 + level // interval,
        "next_level": (level // interval + 1) * interval,
    }


async def specialty_view(user_id) -> dict:
    """作成済み・未作成の候補と現在の能力を返す."""
    state = await read_state(user_id)
    catalog = await get_game_catalog()
    growth = await fetch_resource_growth(user_id)
    resources = {to_uuid(r.resource_id).hex: r for r in growth}
    unlocked = {to_uuid(r).hex for r, levels in state.save.bossClears.items() if levels}
    owned = {r for s in state.save.specialties for r in s.resource_ids}
    skills = {s.id: s for s in catalog.skills}
    return {
        "state": state,
        **await specialty_capacity(user_id),
        "available": [
            {"id": r, "name": resources[r].resource_name}
            for r in sorted(unlocked - owned)
            if r in resources
        ],
        "powers": {r: g.power for r, g in resources.items()},
        "names": {r: g.resource_name for r, g in resources.items()},
        "amounts": {
            s.id: strengthened(
                skills[s.skill_id],
                s,
                resources,
                next(
                    (
                        u.power_coefficient
                        for u in catalog.specialty_skills
                        if u.skill_id == s.skill_id
                    ),
                    1,
                ),
            ).amount
            for s in state.save.specialties
            if s.skill_id in skills
        },
    }


def strengthened(skill, specialty: Specialty, resources: dict, power_coefficient=1):
    """配合順に依存せず、現在の合計Powerだけを数式へ適用する."""
    power = sum(
        max(0, resources[r].power) for r in specialty.resource_ids if r in resources
    )
    return skill.model_copy(
        update={
            "amount": min(
                10000,
                floor(skill.amount + power_coefficient * log1p(power)),
            ),
            "effects": [
                effect.model_copy(
                    update={
                        "amount": min(
                            10000,
                            floor(effect.amount + power_coefficient * log1p(power)),
                        ),
                    },
                )
                for effect in skill.effects
            ],
        },
    )


async def update_specialties(user_id, body: SpecialtyUpdate):
    """一リソース一専門分野を維持したまま、専用APIでのみ更新する."""
    previous = await read_state(user_id)
    if (
        previous.revision != body.revision
        or previous.save.battle
        or previous.save.battleFeedback
        or (previous.save.run and previous.save.run.phase == "battle")
    ):
        raise HTTPException(409, "戦闘外で最新の状態から設定してください。")
    updated = previous.model_copy(deep=True)
    entries = updated.save.specialties
    catalog = await get_game_catalog()
    skills = {s.id: s for s in catalog.skills}
    if body.operation == "create":
        resource = body.resource_id.hex if body.resource_id else ""
        unlocked = {
            to_uuid(r).hex for r, levels in updated.save.bossClears.items() if levels
        }
        skill = skills.get(body.skill_id)
        if (
            resource not in unlocked
            or any(resource in s.resource_ids for s in entries)
            or not skill
            or not skill.enabled
            or not any(
                u.skill_id == skill.id and u.enabled for u in catalog.specialty_skills
            )
        ):
            raise HTTPException(
                422,
                "未作成の攻略済みリソースと専門分野用スキルを選んでください。",
            )
        entries.append(
            Specialty(
                id=f"specialty_{uuid4().hex}",
                name=skill.name,
                resource_ids=[resource],
                skill_id=skill.id,
            ),
        )
    elif body.operation == "equip":
        selected = next((s for s in entries if body.ids == [s.id]), None)
        if not selected:
            raise HTTPException(422, "専門分野がありません。")
        selected.equipped = body.equipped
        if (
            sum(s.equipped for s in entries)
            > (await specialty_capacity(user_id))["slots"]
        ):
            raise HTTPException(422, "専門分野の装備枠が不足しています。")
    elif body.operation == "fuse":
        parents = [s for s in entries if s.id in body.ids]
        if (
            len(set(body.ids)) != FUSION_PARTS
            or len(parents) != FUSION_PARTS
            or body.skill_id not in {s.skill_id for s in parents}
            or body.skill_id not in skills
        ):
            raise HTTPException(
                422,
                "異なる専門分野2つと、残すスキルを選んでください。",
            )
        resources = [r for s in parents for r in s.resource_ids]
        if len(resources) != len(set(resources)):
            raise HTTPException(422, "配合元リソースが重複しています。")
        updated.save.specialties = [s for s in entries if s not in parents] + [
            Specialty(
                id=f"specialty_{uuid4().hex}",
                name=skills[body.skill_id].name,
                resource_ids=sorted(resources),
                skill_id=body.skill_id,
            ),
        ]
    else:
        raise HTTPException(422, "不明な専門分野操作です。")
    async with atomic():
        await persist(user_id, previous, updated)
    return updated
