"""LSP workspaceのローカルTanbunファイル列挙."""

from __future__ import annotations

from pathlib import Path

from pygls.lsp.server import LanguageServer
from pygls.uris import to_fs_path

from tanbun.feature.language_service import SourceDocument

TANBUN_SUFFIXES = frozenset({".kn", ".tb"})


def local_documents(
    server: LanguageServer,
    *,
    current_uri: str,
) -> tuple[SourceDocument, ...]:
    """open中の内容を優先してworkspace内のTanbunファイルを返す."""
    documents: dict[str, SourceDocument] = {}
    current_path = _local_path(current_uri)
    open_paths = set()
    for uri, document in server.workspace.text_documents.items():
        path = _local_path(uri)
        if uri == current_uri or (path is not None and path == current_path):
            continue
        documents[uri] = SourceDocument(uri=uri, text=document.source)
        if path is not None:
            open_paths.add(path)

    root_path = server.workspace.root_path
    if root_path is None:
        return tuple(documents.values())
    root = Path(root_path)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TANBUN_SUFFIXES:
            continue
        resolved = path.resolve()
        uri = resolved.as_uri()
        if resolved == current_path or resolved in open_paths or uri in documents:
            continue
        try:
            documents[uri] = SourceDocument(uri=uri, text=path.read_text())
        except (OSError, UnicodeError):
            continue
    return tuple(documents.values())


def _local_path(uri: str) -> Path | None:
    try:
        return Path(to_fs_path(uri)).resolve()
    except (OSError, ValueError):
        return None
