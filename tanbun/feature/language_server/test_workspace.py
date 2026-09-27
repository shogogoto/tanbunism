"""ローカルworkspace文書列挙のテスト."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from tanbun.feature.language_server.workspace import local_documents


def test_collects_tanbun_files_but_not_unrelated_files(tmp_path) -> None:
    """workspaceから.tb/.knだけを決定的な順序で読む."""
    (tmp_path / "a.tb").write_text("# a\n")
    (tmp_path / "b.kn").write_text("# b\n")
    (tmp_path / "ignored.md").write_text("# ignored\n")
    server = MagicMock()
    server.workspace.root_path = str(tmp_path)
    server.workspace.text_documents = {}

    documents = local_documents(server, current_uri="file:///current.tb")

    assert [document.text for document in documents] == ["# a\n", "# b\n"]


def test_prefers_unsaved_open_document(tmp_path) -> None:
    """disk上の内容よりエディタで編集中の内容を優先する."""
    path = tmp_path / "source.tb"
    path.write_text("# saved\n")
    uri = path.resolve().as_uri()
    server = MagicMock()
    server.workspace.root_path = str(tmp_path)
    server.workspace.text_documents = {
        uri: SimpleNamespace(source="# unsaved\n"),
    }

    documents = local_documents(server, current_uri="file:///current.tb")

    assert [(document.uri, document.text) for document in documents] == [
        (uri, "# unsaved\n"),
    ]


def test_does_not_duplicate_current_document_with_equivalent_uri(tmp_path) -> None:
    """日本語をescapeしたURIと未escape URIを同じファイルとして扱う."""
    path = tmp_path / "日本語.tb"
    path.write_text("# current\n")
    server = MagicMock()
    server.workspace.root_path = str(tmp_path)
    server.workspace.text_documents = {}
    unescaped_uri = f"file://{path.resolve()}"

    documents = local_documents(server, current_uri=unescaped_uri)

    assert documents == ()
