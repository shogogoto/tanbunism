"""一段の辺だけを読み、チェックポイント付きジョブをDBに保存."""

import json
from uuid import uuid4

from neomodel import adb

from .cypher import current_cache
from .domain import LEASE_SECONDS, VERSION, PageRankSettings

LOCK = """
    MERGE (queue:PageRankQueue {key: 'pagerank'})
    SET queue.revision = coalesce(queue.revision, 0) + 1
    WITH queue
"""

OWNED_JOB = """
    MATCH (j:PageRankJob {uid: $job_id})
    SET j.revision = coalesce(j.revision, 0) + 1
    WITH j WHERE j.owner = $owner AND j.status = 'running'
"""


class PageRankStore:
    """HTTPとworkerは異なるDBインスタンスを使用する."""

    def __init__(self, database=adb):
        """呼出元のDBを保持."""
        self.db = database

    async def settings(self) -> PageRankSettings:
        """未設定なら安全な既定値."""
        rows, _ = await self.db.cypher_query(
            "MATCH (q:PageRankQueue {key: 'pagerank'}) RETURN q.settings",
        )
        return (
            PageRankSettings.model_validate_json(rows[0][0])
            if rows and rows[0][0]
            else PageRankSettings()
        )

    async def save_settings(self, settings: PageRankSettings) -> PageRankSettings:
        """負荷制御パラメータを保存."""
        await self.db.cypher_query(
            LOCK
            + """SET queue.settings = $settings,
              queue.max_concurrent_jobs = $concurrent,
              queue.max_pending_jobs = $pending""",
            {
                "settings": settings.model_dump_json(),
                "concurrent": settings.max_concurrent_jobs,
                "pending": settings.max_pending_jobs,
            },
        )
        return settings

    async def resources(self) -> list[dict]:
        """キャッシュの鮮度をResourceの内容・更新日時と比較."""
        rows, _ = await self.db.cypher_query(
            f"""
            MATCH (r:Resource)
            RETURN r.uid, coalesce(r.title, '無題'),
              CASE WHEN r.pagerank_computed_at IS NULL THEN 'missing'
                WHEN {current_cache("r")} THEN 'ready'
                ELSE 'stale' END, toString(r.pagerank_computed_at)
            ORDER BY r.title, r.uid
            """,
        )
        return [
            dict(zip(("uid", "title", "state", "computed_at"), row, strict=True))
            for row in rows
        ]

    async def enqueue(self, ids: list[str], user_id: str) -> dict:
        """FIFO投入。キュー上限・対象検証と登録を一つの書込で行う."""
        ids = list(dict.fromkeys(ids))
        settings = await self.settings()
        uid = uuid4().hex
        rows, _ = await self.db.cypher_query(
            LOCK
            + """
            OPTIONAL MATCH (pending:PageRankJob)
            WHERE pending.status IN ['queued', 'running']
            WITH queue, count(pending) AS pending_count
            CALL () {
              UNWIND $ids AS id
              MATCH (r:Resource {uid: id})
              RETURN count(r) AS resource_count
            }
            WITH queue, pending_count, resource_count
            WHERE pending_count < coalesce(queue.max_pending_jobs, $max_pending)
                AND resource_count = size($ids)
            CREATE (job:PageRankJob {uid: $uid, user_id: $user_id,
              resource_ids: $ids, status: 'queued', completed: 0,
              results: '[]', created: datetime()})
            RETURN job{.*}
            """,
            {
                "ids": ids,
                "uid": uid,
                "user_id": user_id,
                "max_pending": settings.max_pending_jobs,
            },
        )
        if not rows:
            msg = "キューが満杯、または選択したリソースが存在しません。"
            raise ValueError(msg)
        return self.serialize(rows[0][0])

    @staticmethod
    def serialize(job: dict) -> dict:
        """DB専用のリース情報を隠し、日時・結果をJSON化."""
        return {
            "uid": job["uid"],
            "status": job["status"],
            "total": len(job["resource_ids"]),
            "completed": job["completed"],
            "current_title": job.get("current_title"),
            "created": str(job["created"]),
            "results": json.loads(job["results"]),
        }

    async def jobs(self) -> list[dict]:
        """直近30件。完了結果も確認可能."""
        rows, _ = await self.db.cypher_query(
            "MATCH (j:PageRankJob) RETURN j{.*} ORDER BY j.created DESC LIMIT 30",
        )
        return [self.serialize(row[0]) for row in rows]

    async def claim(self, owner: str, maximum: int) -> dict | None:
        """期限切れは再開。調停ノードへの書込ロックで全プロセスの実行数を制御."""
        rows, _ = await self.db.cypher_query(
            LOCK
            + """
            OPTIONAL MATCH (active:PageRankJob {status: 'running'})
            WHERE active.lease_until > datetime()
            WITH queue, count(active) AS active_count
            WHERE active_count < coalesce(queue.max_concurrent_jobs, $maximum)
            MATCH (job:PageRankJob)
            WHERE job.status = 'queued' OR
              (job.status = 'running' AND job.lease_until <= datetime())
            WITH job ORDER BY job.created, job.uid LIMIT 1
            SET job.status = 'running', job.owner = $owner,
              job.lease_until = datetime() + duration({seconds: $lease})
            RETURN job{.*}
            """,
            {"owner": owner, "maximum": maximum, "lease": LEASE_SECONDS},
        )
        return rows[0][0] if rows else None

    async def heartbeat(self, uid: str, owner: str) -> bool:
        """計算中の生存確認。所有権を失ったworkerは結果を書かない."""
        rows, _ = await self.db.cypher_query(
            LOCK
            + OWNED_JOB
            + """
            SET j.lease_until = datetime() + duration({seconds: $lease})
            RETURN j.uid""",
            {"job_id": uid, "owner": owner, "lease": LEASE_SECONDS},
        )
        return bool(rows)

    async def graph(self, resource_id: str, settings: PageRankSettings) -> tuple:
        """現行のSentence・Quotermと参照・論理辺。無制限パス探索を行わない."""
        rows, _ = await self.db.cypher_query(
            "MATCH (r:Resource {uid: $uid}) "
            "RETURN coalesce(r.title, '無題'), r.txt_hash, toString(r.updated)",
            {"uid": resource_id},
        )
        if not rows:
            msg = "リソースが削除されました。"
            raise ValueError(msg)
        title, source_hash, updated = rows[0]
        if source_hash is None or updated is None:
            msg = "リソースのimportが完了していません。読書メモを再importしてください。"
            raise ValueError(msg)
        nodes, _ = await self.db.cypher_query(
            """MATCH (n:Sentence|Quoterm {resource_uid: $uid})
            RETURN n.uid LIMIT $limit""",
            {"uid": resource_id, "limit": settings.max_nodes + 1},
        )
        edges, _ = await self.db.cypher_query(
            """MATCH (a:Sentence|Quoterm {resource_uid: $uid})
              -[e:TO|RESOLVED|REF]->(b:Sentence|Quoterm {resource_uid: $uid})
            RETURN a.uid, b.uid, type(e) LIMIT $limit""",
            {"uid": resource_id, "limit": settings.max_edges + 1},
        )
        if len(nodes) > settings.max_nodes or len(edges) > settings.max_edges:
            msg = (
                "計算サイズ上限を超えています。adminのPageRank設定を確認してください。"
            )
            raise ValueError(msg)
        return title, source_hash, updated, [row[0] for row in nodes], edges

    async def publish(
        self,
        job: dict,
        resource_id: str,
        snapshot: tuple,
        ranks: dict,
    ) -> None:
        """計算中にimportされた結果は捨てる。一括UNWIND書込と鮮度メタデータは原子的."""
        _, source_hash, updated, nodes, _ = snapshot
        rows, _ = await self.db.cypher_query(
            OWNED_JOB
            + """
            WITH j WHERE j.lease_until > datetime()
            MATCH (r:Resource {uid: $uid})
            SET r.pagerank_revision = coalesce(r.pagerank_revision, 0) + 1
            WITH r WHERE r.txt_hash = $hash AND toString(r.updated) = $updated
            CALL (r) {
              UNWIND $ranks AS rank
              MATCH (s:Sentence {uid: rank.uid, resource_uid: r.uid})
              SET s.pagerank = rank.value, s.pagerank_score = rank.score,
                s.pagerank_version = $version
            }
            SET r.pagerank_version = $version, r.pagerank_source_hash = $hash,
              r.pagerank_source_updated = r.updated,
              r.pagerank_computed_at = datetime(),
              r.pagerank_node_count = $count
            RETURN r.uid
            """,
            {
                "job_id": job["uid"],
                "owner": job["owner"],
                "uid": resource_id,
                "hash": source_hash,
                "updated": updated,
                "count": len(nodes),
                "version": VERSION,
                "ranks": [
                    {"uid": uid, "value": value, "score": value * len(nodes)}
                    for uid, value in ranks.items()
                ],
            },
        )
        if not rows:
            msg = (
                "計算中にリソースが更新されたか、処理の所有権が失われました。"
                "再計算してください。"
            )
            raise ValueError(msg)

    async def checkpoint(
        self,
        job: dict,
        results: list[dict],
        *,
        finished: bool = False,
    ) -> bool:
        """成功・失敗とも保存し、再起動で同じ位置から再開."""
        status = "running"
        if finished:
            status = (
                "completed"
                if all(item["error"] is None for item in results)
                else "partial"
            )
        rows, _ = await self.db.cypher_query(
            OWNED_JOB
            + """
            WITH j WHERE j.lease_until > datetime()
            SET j.completed = $completed, j.results = $results, j.status = $status,
              j.current_title = null RETURN j.uid""",
            {
                "job_id": job["uid"],
                "owner": job["owner"],
                "completed": len(results),
                "results": json.dumps(results, ensure_ascii=False),
                "status": status,
            },
        )
        return bool(rows)

    async def current(self, job: dict, title: str) -> None:
        """現在計算中のリソース名を公開."""
        await self.db.cypher_query(
            OWNED_JOB + "SET j.current_title = $title",
            {"job_id": job["uid"], "owner": job["owner"], "title": title},
        )
