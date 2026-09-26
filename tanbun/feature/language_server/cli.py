"""Language ServerのCLI."""

import click

from tanbun.feature.language_server.server import language_server


@click.command("lsp")
def lsp_cmd() -> None:
    """標準入出力でTanbun Language Serverを起動する."""
    language_server.start_io()
