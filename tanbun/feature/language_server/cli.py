"""Language ServerのCLI."""

import click


@click.command("lsp")
def lsp_cmd() -> None:
    """標準入出力でTanbun Language Serverを起動する."""
    from tanbun.feature.language_server.server import language_server  # noqa: PLC0415

    language_server.start_io()
