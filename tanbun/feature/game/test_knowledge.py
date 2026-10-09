"""ゲーム候補と閲覧記録の有効性が一致することを検証."""

from uuid import uuid4

from httpx import AsyncClient
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.user.testing import aauth_header, aregister


@mark_async_test()
async def test_knowledge_validation_matches_exposures(ac: AsyncClient) -> None:
    """古い孤立単文・欠落・他人の単文を除き、検証自体はXPを記録しない."""
    user = await aregister("game-knowledge@example.com")
    other = await aregister("other-knowledge@example.com")
    _, resource = await save_text(user.uid, "# 本\n    現行の単文\n")
    _, outside = await save_text(other.uid, "# 別の本\n    他人の単文\n")
    rows, _ = await adb.cypher_query(
        "MATCH (s:Sentence) WHERE s.resource_uid IN $ids RETURN s.uid, s.resource_uid",
        {"ids": [resource.uid.hex, outside.uid.hex]},
    )
    valid = next(row[0] for row in rows if row[1] == resource.uid.hex)
    foreign = next(row[0] for row in rows if row[1] == outside.uid.hex)
    orphan, missing = uuid4().hex, uuid4().hex
    await adb.cypher_query(
        "CREATE (:Sentence {uid: $uid, val: '孤立単文', resource_uid: $resource})",
        {"uid": orphan, "resource": resource.uid.hex},
    )
    headers = await aauth_header(user.email)
    path = "/game/knowledge/validate"
    body = {
        "resource_id": str(resource.uid),
        "sentence_ids": [valid, orphan, missing, foreign, valid],
    }
    assert (await ac.post(path, json=body)).status_code == 401  # noqa: PLR2004
    response = await ac.post(path, json=body, headers=headers)
    assert response.is_success
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == [valid]
    assert (await ac.get("/dashboard/tanbuns/exposures/today", headers=headers)).json()[
        "count"
    ] == 0
    for uid in [orphan, missing, foreign]:
        assert (
            await ac.post(f"/dashboard/tanbuns/{uid}/exposures", headers=headers)
        ).status_code == 404  # noqa: PLR2004
    assert (
        await ac.post(f"/dashboard/tanbuns/{valid}/exposures", headers=headers)
    ).is_success
    denied = await ac.post(
        path,
        json={**body, "resource_id": str(outside.uid)},
        headers=headers,
    )
    assert denied.json() == []
    await adb.cypher_query(
        "MATCH (s:Sentence {uid: $uid}) DETACH DELETE s",
        {"uid": valid},
    )
    assert (await ac.post(path, json=body, headers=headers)).json() == []
    assert (
        await ac.post(f"/dashboard/tanbuns/{valid}/exposures", headers=headers)
    ).status_code == 404  # noqa: PLR2004
