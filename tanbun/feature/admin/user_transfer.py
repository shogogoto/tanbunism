"""確認済みの空アカウントへのデータ移行。認証情報とUserは保持する."""

import hashlib
import json
from collections import Counter
from uuid import UUID, uuid4

from fastapi import HTTPException
from neomodel.async_.core import AsyncDatabase
from neomodel.exceptions import UniqueProperty
from pydantic import BaseModel, Field

OUTGOING = (
    "CREATE",
    "LEARN",
    "ANSWER",
    "REPORT",
    "ARCHEIVE",
    "REVIEW_SETTINGS",
    "REVIEW_DAY",
    "RECOMMENDATIONS",
)
SCALAR_LABELS = {"TanbunExposure", "ResourceXpEvent", "PageRankJob"}
CACHE_LABELS = {"Archievement", "DailyRecommendation", "ReviewDaySettings"}


class UserTransferRequest(BaseModel):
    """移行先と、プレビューを見たことの確認."""

    target_id: UUID
    source_confirmation: str = Field(min_length=1)
    target_confirmation: str = Field(min_length=1)
    preview_token: str = Field(min_length=64, max_length=64)


class UserTransferPreview(BaseModel):
    """機密の属性は返さず、移行件数と拒否理由だけ表示する."""

    source_id: UUID
    target_id: UUID
    source_email: str
    target_email: str
    counts: dict[str, int]
    blockers: list[str]
    preview_token: str


async def _snapshot(uid: str, db: AsyncDatabase) -> dict:
    rows, _ = await db.cypher_query(
        """MATCH (u:User {uid:$uid})
        RETURN u.email, u.is_active, u.is_superuser""",
        params={"uid": uid},
    )
    if not rows:
        raise HTTPException(404, "ユーザーが見つかりません")
    relations, _ = await db.cypher_query(
        """MATCH (u:User {uid:$uid})-[r]-(n)
        WHERE type(r) <> 'OAUTH' AND NOT n:PushSubscription
        RETURN elementId(r), type(r), startNode(r)=u, labels(n),
            elementId(n), properties(n), properties(r)
        ORDER BY elementId(r)""",
        params={"uid": uid},
    )
    scalars, _ = await db.cypher_query(
        """MATCH (n {user_id:$uid})
        RETURN elementId(n), labels(n), properties(n) ORDER BY elementId(n)""",
        params={"uid": uid},
    )
    resources, _ = await db.cypher_query(
        """MATCH (r:Resource)
        WHERE EXISTS { MATCH (r)-[:PARENT*0..32]->()-[:OWNED]->(:User {uid:$uid}) }
        RETURN elementId(r), r.title, r.resource_key ORDER BY elementId(r)""",
        params={"uid": uid},
    )
    reports, _ = await db.cypher_query(
        """MATCH (:User {uid:$uid})-[:REPORT]->(r:QuizReport)
        RETURN elementId(r), r.key ORDER BY elementId(r)""",
        params={"uid": uid},
    )
    return {
        "user": rows[0],
        "relations": relations,
        "scalars": scalars,
        "resources": resources,
        "reports": reports,
    }


def _schema_blockers(snapshot: dict) -> list[str]:
    reasons = []
    for _, kind, outgoing, _, _, _, _ in snapshot["relations"]:
        if not ((outgoing and kind in OUTGOING) or (not outgoing and kind == "OWNED")):
            reasons.append(f"未対応のユーザー関連があります: {kind}")
    for _, labels, properties in snapshot["scalars"]:
        if not set(labels) & SCALAR_LABELS:
            reasons.append(f"未対応のuser_id属性があります: {', '.join(labels)}")
        if "PageRankJob" in labels and properties.get("status") in {
            "queued",
            "running",
            "pending",
        }:
            reasons.append("PageRankの処理が終了してから移行してください")
    return reasons


def _blockers(source: dict, target: dict, source_uid: str) -> list[str]:
    reasons = _schema_blockers(source) + _schema_blockers(target)
    # 空の推薦セット・日別設定・集計キャッシュはログイン直後にも生成される。
    for _, _, _, labels, _, properties, _ in target["relations"]:
        if "Notification" in labels:
            continue
        if set(labels) & CACHE_LABELS and not properties.get("ids"):
            continue
        reasons.append("移行先に既存の学習データがあります。統合・上書きはできません")
        break
    if target["scalars"] or target["resources"]:
        reasons.append("移行先に既存の学習実績またはリソースがあります")
    for _, labels, properties in source["scalars"]:
        if set(labels) & {"TanbunExposure", "ResourceXpEvent"} and not str(
            properties.get("key", ""),
        ).startswith(f"{source_uid}:"):
            reasons.append("見たよ・XPのキーが不正なため移行できません")
    for _, key in source["reports"]:
        if not key or not key.startswith(f"{source_uid}:"):
            reasons.append("クイズ報告のキーが不正なため移行できません")
    titles = [row[1] for row in source["resources"]]
    if any(not title for title in titles) or len(titles) != len(set(titles)):
        reasons.append("読書メモのタイトルが欠けているか重複しています")
    return list(dict.fromkeys(reasons))


async def preview_user_transfer(
    source_id: UUID,
    target_id: UUID,
    db: AsyncDatabase | None = None,
) -> UserTransferPreview:
    """書き込みを行わないプレビュー。トークンはIDと内容の指紋."""
    if db is None:
        connection = AsyncDatabase()
        try:
            async with connection.transaction:
                return await preview_user_transfer(source_id, target_id, connection)
        finally:
            await connection.close_connection()
    if source_id == target_id:
        raise HTTPException(409, "同じユーザーには移行できません")
    source, target = (
        await _snapshot(source_id.hex, db),
        await _snapshot(target_id.hex, db),
    )
    counts = Counter()
    nodes = {row[4]: row[3] for row in source["relations"]}
    nodes.update({row[0]: row[1] for row in source["scalars"]})
    for labels in nodes.values():
        counts.update(labels)
    counts["Resource"] = len(source["resources"])
    content = json.dumps(
        [str(source_id), str(target_id), source, target],
        default=str,
        sort_keys=True,
    )
    return UserTransferPreview(
        source_id=source_id,
        target_id=target_id,
        source_email=source["user"][0],
        target_email=target["user"][0],
        counts=dict(counts),
        blockers=_blockers(source, target, source_id.hex),
        preview_token=hashlib.sha256(content.encode()).hexdigest(),
    )


async def transfer_user_data(
    source_id: UUID,
    body: UserTransferRequest,
    actor_id: UUID,
) -> UserTransferPreview:
    """他リクエストと共有しない接続で、例外時には全体をロールバック."""
    db = AsyncDatabase()
    try:
        async with db.transaction:
            return await _transfer_user_data(source_id, body, actor_id, db)
    except UniqueProperty as error:
        raise HTTPException(
            409,
            "キーが重複しているため移行できません。変更は取り消しました",
        ) from error
    finally:
        await db.close_connection()


async def _transfer_user_data(
    source_id: UUID,
    body: UserTransferRequest,
    actor_id: UUID,
    db: AsyncDatabase,
) -> UserTransferPreview:
    # 同じ対象への並行移行・XP加点・推薦更新を直列化し、再検査する。
    for uid in sorted({source_id.hex, body.target_id.hex}):
        await db.cypher_query(
            """MATCH (u:User {uid:$uid})
            SET u.resource_xp_revision=coalesce(u.resource_xp_revision,0)+1,
                u.review_settings_revision=coalesce(u.review_settings_revision,0)+1""",
            params={"uid": uid},
        )
    preview = await preview_user_transfer(source_id, body.target_id, db)
    if (
        body.source_confirmation != preview.source_email
        or body.target_confirmation != preview.target_email
    ):
        raise HTTPException(409, "確認用メールアドレスが一致しません")
    if preview.blockers:
        raise HTTPException(409, " / ".join(preview.blockers))
    if body.preview_token != preview.preview_token:
        raise HTTPException(409, "データが変わりました。移行内容を再確認してください")
    if not any(preview.counts.values()):
        raise HTTPException(409, "移行するデータがありません")
    params = {"source": source_id.hex, "target": body.target_id.hex}
    # 移行先の空の派生キャッシュだけ破棄。認証・通知・Push登録は保持。
    await db.cypher_query(
        """MATCH (:User {uid:$target})-[:ARCHEIVE|RECOMMENDATIONS|REVIEW_DAY]->(cache)
        DETACH DELETE cache""",
        params=params,
    )
    await db.cypher_query(
        """MATCH (r:Resource)
        WHERE EXISTS { MATCH (r)-[:PARENT*0..32]->()-[:OWNED]->(:User {uid:$source}) }
        SET r.resource_key=$target + ':' + r.title""",
        params=params,
    )
    await db.cypher_query(
        """MATCH (n {user_id:$source})
        SET n.user_id=$target
        FOREACH (_ IN CASE WHEN n:TanbunExposure OR n:ResourceXpEvent
            THEN [1] ELSE [] END |
            SET n.key=$target + substring(n.key, size($source)))""",
        params=params,
    )
    await db.cypher_query(
        """MATCH (:User {uid:$source})-[:REPORT]->(n:QuizReport)
        SET n.key=$target + substring(n.key, size($source))""",
        params=params,
    )
    for kind in OUTGOING:
        await db.cypher_query(
            f"""MATCH (s:User {{uid:$source}})-[r:{kind}]->(n),
                (t:User {{uid:$target}})
            CREATE (t)-[copy:{kind}]->(n)
            SET copy=properties(r) DELETE r""",
            params=params,
        )
    await db.cypher_query(
        """MATCH (n:Notification)-[:OWNED]->(:User {uid:$source})
        WHERE n.href STARTS WITH $old_profile
        SET n.href=$new_profile + substring(n.href, size($old_profile))""",
        params={
            **params,
            "old_profile": f"/user/{source_id}",
            "new_profile": f"/user/{body.target_id}",
        },
    )
    await db.cypher_query(
        """MATCH (n)-[r:OWNED]->(:User {uid:$source}), (t:User {uid:$target})
        WHERE NOT n:PushSubscription
        CREATE (n)-[copy:OWNED]->(t) SET copy=properties(r) DELETE r""",
        params=params,
    )
    await db.cypher_query(
        """CREATE (:UserDataTransfer {uid:$id, source_id:$source, target_id:$target,
            actor_id:$actor, transferred_at:datetime(), counts:$counts})""",
        params={
            **params,
            "id": uuid4().hex,
            "actor": actor_id.hex,
            "counts": json.dumps(preview.counts),
        },
    )
    return preview
