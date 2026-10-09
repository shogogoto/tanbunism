"""保存済み・キャッシュ済み候補を現行DBで検査する."""

from uuid import UUID

from neomodel import adb
from pydantic import BaseModel, Field

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.tanbun.repo.reviewable import q_reviewable_sentences


class KnowledgeValidation(BaseModel):
    """一括検査は限定されたサイズと対象リソースで行う."""

    resource_id: UUID
    sentence_ids: list[UUID] = Field(max_length=500)


async def valid_knowledge(user_id: UUIDy, body: KnowledgeValidation) -> list[str]:
    """欠落・孤立・他人所有・別リソースの単文には閲覧記録を許可しない."""
    if not body.sentence_ids:
        return []
    rows, _ = await adb.cypher_query(
        q_reviewable_sentences() + " RETURN DISTINCT sentence.uid",
        params={
            "user_id": to_uuid(user_id).hex,
            "resource_id": body.resource_id.hex,
            "sentence_ids": list(dict.fromkeys(uid.hex for uid in body.sentence_ids)),
        },
    )
    return [row[0] for row in rows]
