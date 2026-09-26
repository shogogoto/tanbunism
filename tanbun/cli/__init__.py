"""cli root."""

from __future__ import annotations

import click

from tanbun.feature.entry.cli import anchor_cmd, sync_cmd
from tanbun.feature.language_server.cli import lsp_cmd
from tanbun.feature.user.cli import user_cli

from .options.completion import complete_option
from .options.help_all import help_all_option
from .view import view_cli

__version__ = "0.0.0"


@click.group()
@click.version_option(version=__version__, prog_name="tanbunism")
@help_all_option()
@complete_option()
def cli() -> None:
    """Tanbunism CLI."""


@cli.command("config")
def config_cmd() -> None:
    """設定内容の確認."""
    from tanbun.config import LocalConfig  # noqa: PLC0415
    from tanbun.config.env import Settings  # noqa: PLC0415

    s = Settings()
    click.echo(s.config_file)
    c = LocalConfig.load()
    click.echo(c.model_dump_json(indent=2))


cli.add_command(view_cli)
cli.add_command(user_cli)
cli.add_command(anchor_cmd)
cli.add_command(sync_cmd)
cli.add_command(lsp_cmd)

if __name__ == "__main__":
    cli()
