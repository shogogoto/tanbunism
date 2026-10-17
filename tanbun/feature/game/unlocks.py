"""探索達成度によるダンジョンの恒久開放."""
# ruff: noqa: N815

from secrets import choice
from typing import TYPE_CHECKING, Literal

from neomodel import adb
from pydantic import BaseModel, Field, model_validator

from tanbun.feature.domain.types import UUIDy, to_uuid

if TYPE_CHECKING:
    from .state import GameSave

PLACES_PER_UNLOCK = 15
MAX_UNLOCKED_DUNGEONS = 1000
ACHIEVEMENTS_PER_UNLOCK = 3


class DungeonUnlock(BaseModel):
    """どのダンジョンの達成度で道が開いたかを保存する."""

    sourceId: str = Field(max_length=64)
    resourceId: str = Field(max_length=64)
    name: str = Field(max_length=1000)
    achievement: int = Field(ge=0)
    source: Literal["exploration", "item"] = "exploration"

    @model_validator(mode="after")
    def valid_achievement(self):
        """アイテム開放と探索の達成度報酬を区別する."""
        if self.source == "exploration" and self.achievement < ACHIEVEMENTS_PER_UNLOCK:
            msg = "探索による開放は達成度3以上です。"
            raise ValueError(msg)
        return self


async def discover_dungeons(user_id: UUIDy, save: "GameSave") -> bool:
    """所有リソースから抽選し、同じ節目を二度消費しない."""
    if len(save.unlockedDungeons) >= MAX_UNLOCKED_DUNGEONS:
        return False
    claimed = {
        (item.sourceId.replace("-", "").lower(), item.achievement)
        for item in save.unlockedDungeons
        if item.source == "exploration"
    }
    sizes = {
        key.replace("-", "").lower(): len(value.places)
        for key, value in save.maps.items()
    }
    if save.run:
        source = save.run.resourceId.replace("-", "").lower()
        sizes[source] = max(sizes.get(source, 0), len(set(save.run.readIds)))
    pending = [
        (source, step * 3)
        for source, count in sizes.items()
        for step in range(1, count // PLACES_PER_UNLOCK + 1)
        if (source, step * 3) not in claimed
    ]
    if not pending:
        return False
    rows, _ = await adb.cypher_query(
        """MATCH (resource:Resource)-[:PARENT*0..]->()-[:OWNED]->(:User {uid:$user})
        WHERE EXISTS { MATCH (s:Sentence {resource_uid: resource.uid}) }
        RETURN DISTINCT resource.uid, coalesce(resource.title, 'ダンジョン')
        ORDER BY resource.uid""",
        {"user": to_uuid(user_id).hex},
    )
    eligible = {row[0]: row[1] for row in rows}
    opened = {
        key.replace("-", "").lower()
        for key in [*save.visitedDungeons, *save.maps, *save.dungeons, *save.clears]
    }
    opened.update(
        item.resourceId.replace("-", "").lower() for item in save.unlockedDungeons
    )
    if save.run:
        opened.add(save.run.resourceId.replace("-", "").lower())
    changed = False
    for source, achievement in pending:
        if len(save.unlockedDungeons) >= MAX_UNLOCKED_DUNGEONS:
            break
        candidates = sorted(set(eligible) - opened)
        if source not in eligible or not candidates:
            continue
        target = choice(candidates)
        save.unlockedDungeons.append(
            DungeonUnlock(
                sourceId=source,
                resourceId=target,
                name=eligible[target][:1000],
                achievement=achievement,
            ),
        )
        opened.add(target)
        changed = True
    return changed
