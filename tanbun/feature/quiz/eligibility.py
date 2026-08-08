"""Eligibility rules shared by quiz targets and options."""

from collections.abc import Iterable
from uuid import UUID

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.parsing.sysnet.sysnode import DUMMY_SENTENCE


async def filter_defined_sentence_ids(sent_ids: Iterable[UUIDy]) -> list[UUID]:
    """Keep sentence IDs that contain an actual explanatory sentence."""
    ids = [to_uuid(uid).hex for uid in sent_ids]
    if not ids:
        return []
    q = """
        UNWIND range(0, size($sent_ids) - 1) AS position
        MATCH (sent: Sentence {uid: $sent_ids[position]})
        WHERE sent.val <> $dummy_sentence
        RETURN sent.uid
        ORDER BY position
    """
    rows, _ = await adb.cypher_query(
        q,
        params={"sent_ids": ids, "dummy_sentence": DUMMY_SENTENCE},
    )
    return [to_uuid(row[0]) for row in rows]
