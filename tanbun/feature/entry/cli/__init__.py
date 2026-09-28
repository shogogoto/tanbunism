"""sysnetの永続化."""

import click

DEFAULT_UPLOAD_EXTENSIONS = ("txt", "md", "kn", "tb")


@click.command("anchor")
def anchor_cmd() -> None:
    """カレントディレクトリをDBと同期するファイルパスとして設定."""
    # いちいちcurrent directoryで同期するのは間違いの元なので
    #   link path を予め設定させる
    from .proc import link_proc  # noqa: PLC0415

    link_proc()


@click.command("sync")
@click.option(
    "-e",
    "--extension",
    "extensions",
    multiple=True,
    default=DEFAULT_UPLOAD_EXTENSIONS,
    show_default=True,
    help="対象拡張子。複数回指定できます。",
)
@click.option("-g", "--glob", default=None, help="ファイルパスを絞り込むglob")
@click.option(
    "-h",
    "--hide-error",
    is_flag=True,
    default=False,
    help="パースエラーの詳細を隠します。",
)
@click.option("--force", is_flag=True, help="変更がないファイルも再送します。")
def sync_cmd(
    extensions: tuple[str, ...],
    glob: str | None,
    *,
    hide_error: bool,
    force: bool,
) -> None:
    """ファイルシステムと同期."""
    from .proc import sync_proc  # noqa: PLC0415

    sync_proc(
        glob,
        show_error=not hide_error,
        extensions=extensions,
        force=force,
    )
