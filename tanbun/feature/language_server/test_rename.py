"""LSPのprepareRename・rename adapterと未保存文書・文字位置の検証."""
# ruff: noqa: PLR2004

import pytest
from lsprotocol import types
from pygls.exceptions import JsonRpcInvalidParams

from .server import create_server


def server_with_document(tmp_path, text):
    """実際のpygls workspaceに未保存の文書を開く."""
    server = create_server()
    list(
        server.protocol.lsp_initialize(
            types.InitializeParams(
                capabilities=types.ClientCapabilities(),
                root_uri=tmp_path.as_uri(),
            ),
        ),
    )
    uri = (tmp_path / "日本語.tb").as_uri()
    server.workspace.put_text_document(
        types.TextDocumentItem(
            uri=uri,
            language_id="tanbun",
            version=2,
            text=text,
        ),
    )
    return server, types.TextDocumentIdentifier(uri=uri)


def test_rename_features_and_utf16_workspace_edits_prefer_unsaved_text(tmp_path):
    """絵文字の後のUTF-16位置、未保存の外部文書、ディスク非変更を確認する."""
    path = tmp_path / "source.kn"
    path.write_text("# source\n  古い定義: saved\n")
    server, identifier = server_with_document(tmp_path, "# current\n  😀{用語}\n")
    server.workspace.put_text_document(
        types.TextDocumentItem(
            uri=path.as_uri(),
            language_id="tanbun",
            version=3,
            text="# source\n  用語: unsaved\n  {用語}\n",
        ),
    )
    features = server.protocol.fm.features
    assert server.protocol.server_capabilities.rename_provider
    assert server.protocol.fm.feature_options[
        types.TEXT_DOCUMENT_RENAME
    ].prepare_provider
    prepared = features[types.TEXT_DOCUMENT_PREPARE_RENAME](
        types.PrepareRenameParams(
            text_document=identifier,
            position=types.Position(line=1, character=5),
        ),
    )
    assert prepared.placeholder == "用語"
    assert prepared.range == types.Range(
        start=types.Position(line=1, character=5),
        end=types.Position(line=1, character=7),
    )
    edit = features[types.TEXT_DOCUMENT_RENAME](
        types.RenameParams(
            text_document=identifier,
            position=types.Position(line=1, character=5),
            new_name="新版",
        ),
    )
    assert set(edit.changes) == {identifier.uri, path.as_uri()}
    assert len(edit.changes[identifier.uri]) == 1
    assert edit.changes[identifier.uri][0].range == prepared.range
    assert len(edit.changes[path.as_uri()]) == 2
    assert all(
        item.new_text == "新版" for items in edit.changes.values() for item in items
    )
    assert path.read_text() == "# source\n  古い定義: saved\n"
    assert "{用語}" in server.workspace.get_text_document(identifier.uri).source


def test_closed_workspace_files_are_included_but_unrelated_files_are_not(tmp_path):
    """編集中でない.tb/.knも編集案に含め、他の拡張子には触れない."""
    (tmp_path / "source.kn").write_text("# source\n  用語: 定義\n")
    (tmp_path / "note.md").write_text("{用語}\n")
    server, identifier = server_with_document(tmp_path, "# current\n  {用語}\n")
    server.workspace.put_text_document(
        types.TextDocumentItem(
            uri=(tmp_path / "note.md").as_uri(),
            language_id="markdown",
            version=1,
            text="{用語}\n",
        ),
    )
    result = server.protocol.fm.features[types.TEXT_DOCUMENT_RENAME](
        types.RenameParams(
            text_document=identifier,
            position=types.Position(line=1, character=3),
            new_name="新版",
        ),
    )
    assert len(result.changes) == 2
    assert (tmp_path / "note.md").as_uri() not in result.changes


def test_unsupported_positions_and_invalid_names_are_not_edited(tmp_path):
    """prepareはnullを返し、直接の不正renameはLSPの入力エラーとして返す."""
    server, identifier = server_with_document(tmp_path, "# current\n  用語: 定義\n")
    features = server.protocol.fm.features
    assert (
        features[types.TEXT_DOCUMENT_PREPARE_RENAME](
            types.PrepareRenameParams(
                text_document=identifier,
                position=types.Position(line=0, character=0),
            ),
        )
        is None
    )
    with pytest.raises(JsonRpcInvalidParams):
        features[types.TEXT_DOCUMENT_RENAME](
            types.RenameParams(
                text_document=identifier,
                position=types.Position(line=1, character=2),
                new_name="bad:name",
            ),
        )
