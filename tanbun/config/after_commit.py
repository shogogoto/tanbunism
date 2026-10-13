"""Request-local hooks for external side effects after a successful DB commit."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

type CommitHook = Callable[[], Awaitable[None]]
_hooks: ContextVar[list[CommitHook] | None] = ContextVar(
    "after_commit_hooks",
    default=None,
)


def after_commit(hook: CommitHook) -> bool:
    """Register only within an HTTP transaction; durable jobs cover other callers."""
    hooks = _hooks.get()
    if hooks is not None:
        hooks.append(hook)
        return True
    return False


@contextmanager
def commit_hooks() -> Iterator[list[CommitHook]]:
    """Isolate concurrent requests.

    Yields:
        Hooks shared with nested request tasks.

    """
    hooks: list[CommitHook] = []
    token = _hooks.set(hooks)
    try:
        yield hooks
    finally:
        _hooks.reset(token)
