"""neomodel 5.5のトランザクション状態を非同期タスク・HTTP単位で分離する."""

from asyncio import current_task
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps

from neomodel import adb
from neomodel.async_.core import AsyncDatabase


@dataclass
class TransactionState:
    """HTTP内部の子タスクでは共有し、別リクエストとは共有しない."""

    owner: object = None
    shared: bool = False
    expired: bool = False
    transaction: object = None
    session: object = None
    rollback_only: bool = False


def _task():
    try:
        return current_task()
    except RuntimeError:
        return None


def _variable(db):
    variable = db.__dict__.get("_transaction_context")
    if variable is None:
        variable = ContextVar("neomodel_transaction", default=None)
        db.__dict__["_transaction_context"] = variable
    return variable


def _state(db):
    variable = _variable(db)
    state = variable.get()
    if (
        state is None
        or state.expired
        or (not state.shared and state.owner is not _task())
    ):
        state = TransactionState(owner=_task())
        variable.set(state)
    return state


def configure_transaction_isolation():
    """5.5専用互換境界。接続driverは共有し、transaction/sessionだけ分離する."""
    if isinstance(getattr(AsyncDatabase, "_active_transaction", None), property):
        return
    for attribute, field in (
        ("_active_transaction", "transaction"),
        ("_session", "session"),
    ):

        def get(db, name=field):
            return getattr(_state(db), name)

        def set_value(db, value, name=field):
            setattr(_state(db), name, value)

        setattr(AsyncDatabase, attribute, property(get, set_value))
    for method in ("commit", "rollback"):
        original = getattr(AsyncDatabase, method)

        @wraps(original)
        async def finalize(db, *args, _original=original, **kwargs):
            try:
                return await _original(db, *args, **kwargs)
            finally:
                # close()自体が失敗・キャンセルしても閉じた参照は残さない。
                db._active_transaction = None  # noqa: SLF001
                db._session = None  # noqa: SLF001

        setattr(AsyncDatabase, method, finalize)


@contextmanager
def database_scope():
    """BaseHTTPMiddlewareの子タスクまで同じHTTPのtransactionを伝える."""
    variable = _variable(adb)
    state = TransactionState(owner=_task(), shared=True)
    token = variable.set(state)
    try:
        yield
    finally:
        state.expired = True
        variable.reset(token)


@asynccontextmanager
async def atomic():
    """HTTP transactionがあれば参加、CLI・workerでは自分で開始・確定する."""
    state = _state(adb)
    if adb._active_transaction is not None:  # noqa: SLF001
        try:
            yield
        except BaseException:
            state.rollback_only = True
            raise
        return
    await adb.begin()
    state.rollback_only = False
    try:
        yield
        if state.rollback_only:
            msg = "Transaction marked for rollback"
            raise RuntimeError(msg)  # noqa: TRY301
        await adb.commit()
    except BaseException:
        if adb._active_transaction is not None:  # noqa: SLF001
            await adb.rollback()
        raise


def transaction_requires_rollback():
    """内側で捕捉された失敗をHTTP側でも確定させない."""
    return _state(adb).rollback_only
