"""file -> sentnet 変換が責務."""

import time
from collections.abc import Generator
from contextlib import contextmanager
from cProfile import Profile

from tanbun.feature.domain.errors import DomainError
from tanbun.feature.parsing.issue import exception_to_parse_issue
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.tree2net import parse2net


def try_parse2net(s: str) -> SysNet:
    """エラーを握りつぶしたパース."""
    try:
        return parse2net(s)
    except Exception as e:
        issue = exception_to_parse_issue(s, e)
        raise DomainError(msg=issue.display_message()) from e


@contextmanager
def profile() -> Generator:
    """パフォーマンス計測してstdout."""
    pr = Profile()
    pr.enable()
    try:
        yield pr
    finally:
        pr.disable()
        pr.print_stats(sort="cumtime")


@contextmanager
def elapsed_time(name: str) -> Generator:
    """時間測定."""
    start = time.time()
    yield
    end = time.time()
    print(f"{name}: {end - start:.3f}秒")  # noqa: T201
