"""リソース削除."""

from neomodel.async_.core import AsyncDatabase

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.entry.resource.repo.retirement import retire_referenced_sentences


async def delete_resource_unders(resource_uid: UUIDy):
    """リソース配下の細々としたノードを削除."""
    q = """
        MATCH (s:Sentence {resource_uid: $uid})
        OPTIONAL MATCH (s)-[:WHEN|BY|WHERE]->*(n:Interval|Sentence) // 付荷情報
        OPTIONAL MATCH (s)<-[:DEF]-(t1:Term)
        OPTIONAL MATCH (t1)-[:ALIAS]->*(t2:Term)
        DETACH DELETE s, n, t1, t2
    """
    _rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={"uid": to_uuid(resource_uid).hex},
    )


async def delete_resource_tops(resource_uid: UUIDy):
    """リソースを削除."""
    q = """
        MATCH (r:Resource {uid: $uid})
        OPTIONAL MATCH (r)-[:BELOW|SIBLING]->*(h:Head)
        OPTIONAL MATCH (r)-[:STATS]->(s:ResourceStatsCache)
        OPTIONAL MATCH (plan:StudyPlan {auto_resource_uid: $uid})
        DETACH DELETE r, h, s, plan
    """
    _rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={"uid": to_uuid(resource_uid).hex},
    )


async def delete_resource(resource_uid: UUIDy) -> None:
    """リソースを削除."""
    await retire_referenced_sentences(resource_uid)
    await delete_resource_unders(resource_uid)
    await delete_resource_tops(resource_uid)
