"""補完とhelpはコマンドの実処理に依存せず起動できる."""

import os
import subprocess  # noqa: S404 - fixed Python command for startup regression tests
import sys

import pytest

STARTUP = """
import sys

class RejectHeavyImports:
    def find_spec(self, fullname, path=None, target=None):
        blocked = (
            'httpx', 'requests', 'pydantic', 'pydantic_settings',
            'neomodel', 'neo4j', 'pygls', 'lsprotocol', 'networkx', 'lark',
            'tanbun.config', 'tanbun.feature.language_server.server',
            'tanbun.feature.parsing.check.domain', 'tanbun.feature.parsing.issue',
            'tanbun.feature.user.oauth.browse',
        )
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise RuntimeError('CLI startup imported ' + fullname)

sys.meta_path.insert(0, RejectHeavyImports())
from tanbun.cli import cli
cli.main(prog_name='tb')
"""


def run_startup(tmp_path, args=(), completion=None):
    """既にimport済みのpytestプロセスから隔離して検証する."""
    env = dict(os.environ)
    for key in (
        "_TB_COMPLETE",
        "COMP_WORDS",
        "COMP_CWORD",
        "NEO4J_URL",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "KNOWDE_URL",
    ):
        env.pop(key, None)
    if completion:
        env.update(completion)
    result = subprocess.run(  # noqa: S603 - fixed interpreter and test inputs
        [sys.executable, "-c", STARTUP, *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("--help",),
        ("--help-all",),
        ("game", "--help"),
        ("game", "import", "--help"),
        ("user", "google-sso", "--help"),
        ("lsp", "--help"),
        ("check", "--help"),
        ("view", "score", "--help"),
    ],
)
def test_help_does_not_import_command_implementations(tmp_path, args):
    """help-allで全コマンドを走査しても実装を読み込まない."""
    assert "Usage:" in run_startup(tmp_path, args)


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
@pytest.mark.parametrize(
    ("words", "index", "partial", "expected"),
    [
        ("tb ", "1", "", ("game", "lsp", "check", "user", "view")),
        ("tb game ", "2", "", ("export", "import")),
        ("tb game import --", "3", "--", ("--dry-run", "--yes", "--backup")),
        ("tb user google-sso --", "3", "--", ("--no-browser", "--token-file")),
    ],
)
def test_completion_does_not_import_command_implementations(
    tmp_path,
    *,
    shell,
    words,
    index,
    partial,
    expected,
):
    """ルートと下位コマンドの補完を、候補を維持したまま軽量に保つ."""
    output = run_startup(
        tmp_path,
        completion={
            "_TB_COMPLETE": f"{shell}_complete",
            "COMP_WORDS": words,
            "COMP_CWORD": partial if shell == "fish" else index,
        },
    )
    for candidate in expected:
        assert candidate in output
