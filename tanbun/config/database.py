"""neomodelの全非同期DB操作にサーバー側の期限を付ける境界アダプター.

neomodel 5.5はbegin()の引数をsession()へ渡すため、timeoutを直接
指定できない。接続生成の一箇所でdriverを包み、ORM・Cypher・再接続を
同じ方針にする。neomodel更新時はこの互換境界のテストを確認すること。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps

from neo4j import Query
from neomodel.async_.core import AsyncDatabase


@dataclass(frozen=True)
class DatabaseBudget:
    """トランザクションの時間上限と監査用情報."""

    timeout: float
    operation: str = "database"


_default_budget = DatabaseBudget(30)
_current_budget: ContextVar[DatabaseBudget | None] = ContextVar(
    "database_budget",
    default=None,
)
_installed = False


@contextmanager
def database_budget(timeout: float, operation: str):
    """リクエスト単位の期限を、並行処理間で混ぜずに伝える."""
    token = _current_budget.set(DatabaseBudget(timeout, operation))
    try:
        yield
    finally:
        _current_budget.reset(token)


def is_database_timeout(error: Exception) -> bool:
    """Neo4jがサーバー側の期限超過で終了した場合だけ判定."""
    return "TransactionTimedOut" in (getattr(error, "code", None) or "")


class DeadlineSession:
    """明示トランザクションと暗黙クエリに同じ期限を適用."""

    def __init__(self, session, budget: DatabaseBudget):
        """期限と委譲先を保持."""
        self.session = session
        self.budget = budget

    def __getattr__(self, name):
        """接続操作を委譲."""
        return getattr(self.session, name)

    async def __aenter__(self):
        """セッションを開く."""
        await self.session.__aenter__()
        return self

    async def __aexit__(self, *args):
        """セッションを閉じる."""
        return await self.session.__aexit__(*args)

    def _options(self, timeout=None, metadata=None):
        # 明示的に0(無期限)が指定されても共通の上限を外さない。
        limit = (
            min(timeout, self.budget.timeout)
            if timeout and timeout > 0
            else self.budget.timeout
        )
        return {
            "timeout": limit,
            "metadata": {
                **(metadata or {}),
                "app": "tanbunism",
                "operation": self.budget.operation,
            },
        }

    async def begin_transaction(self, **kwargs):
        """サーバー期限付きで明示トランザクションを開始."""
        kwargs.update(self._options(kwargs.get("timeout"), kwargs.get("metadata")))
        try:
            return await self.session.begin_transaction(**kwargs)
        except BaseException:
            await self.session.close()
            raise

    async def run(self, query, parameters=None, **kwargs):
        """暗黙トランザクションにQueryの期限情報を渡す."""
        if isinstance(query, Query):
            options = self._options(query.timeout, query.metadata)
            query = Query(query.text, **options)
        else:
            query = Query(query, **self._options())
        return await self.session.run(query, parameters=parameters, **kwargs)


class DeadlineDriver:
    """neomodelの接続・クローズ等は委譲し、session生成のみ包む."""

    def __init__(self, driver):
        """接続元driverを保持."""
        self.driver = driver

    def __getattr__(self, name):
        """driver操作を委譲."""
        return getattr(self.driver, name)

    def session(self, **kwargs):
        """現在の処理に対応する期限付きセッションを作る."""
        return DeadlineSession(
            self.driver.session(**kwargs),
            _current_budget.get() or _default_budget,
        )


def configure_database_deadlines(timeout: float) -> None:
    """設定時に一度だけ互換アダプターを登録する."""
    global _default_budget, _installed  # noqa: PLW0603
    _default_budget = DatabaseBudget(timeout)
    if _installed:
        return
    original = AsyncDatabase._parse_driver_from_url  # noqa: SLF001

    @wraps(original)
    def create_driver(database, url):
        original(database, url)
        database.driver = DeadlineDriver(database.driver)

    AsyncDatabase._parse_driver_from_url = create_driver  # noqa: SLF001
    _installed = True
