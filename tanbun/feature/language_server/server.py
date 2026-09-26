"""pyglsとTanbun診断の接続層."""

from __future__ import annotations

import asyncio

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from tanbun.feature.language_server.diagnostics import TanbunDiagnostic, diagnose

SERVER_NAME = "tanbun-language-server"
SERVER_VERSION = "0.1.0"
DIAGNOSTIC_DELAY_SECONDS = 0.3


def create_server() -> LanguageServer:
    """Tanbun language serverを作成する."""
    server = LanguageServer(SERVER_NAME, SERVER_VERSION)
    pending: dict[str, asyncio.Task[None]] = {}

    def cancel_pending(uri: str) -> None:
        task = pending.pop(uri, None)
        if task is not None:
            task.cancel()

    async def publish_after_delay(uri: str, version: int) -> None:
        try:
            await asyncio.sleep(DIAGNOSTIC_DELAY_SECONDS)
            document = server.workspace.get_text_document(uri)
            _publish(server, uri, document.source, version)
        finally:
            if pending.get(uri) is asyncio.current_task():
                pending.pop(uri, None)

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(
        ls: LanguageServer,
        params: types.DidOpenTextDocumentParams,
    ) -> None:
        cancel_pending(params.text_document.uri)
        _publish(ls, params.text_document.uri, params.text_document.text)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(
        ls: LanguageServer,
        params: types.DidChangeTextDocumentParams,
    ) -> None:
        uri = params.text_document.uri
        cancel_pending(uri)
        pending[uri] = asyncio.create_task(
            publish_after_delay(uri, params.text_document.version),
        )

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def did_save(
        ls: LanguageServer,
        params: types.DidSaveTextDocumentParams,
    ) -> None:
        uri = params.text_document.uri
        cancel_pending(uri)
        document = ls.workspace.get_text_document(uri)
        _publish(ls, uri, document.source, getattr(document, "version", None))

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    def did_close(
        ls: LanguageServer,
        params: types.DidCloseTextDocumentParams,
    ) -> None:
        cancel_pending(params.text_document.uri)
        _publish_diagnostics(ls, params.text_document.uri, [])

    return server


def _publish(
    server: LanguageServer,
    uri: str,
    text: str,
    version: int | None = None,
) -> None:
    _publish_diagnostics(
        server,
        uri,
        [_to_lsp_diagnostic(text, item) for item in diagnose(text)],
        version,
    )


def _publish_diagnostics(
    server: LanguageServer,
    uri: str,
    diagnostics: list[types.Diagnostic],
    version: int | None = None,
) -> None:
    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(
            uri=uri,
            diagnostics=diagnostics,
            version=version,
        ),
    )


def _to_lsp_diagnostic(text: str, diagnostic: TanbunDiagnostic) -> types.Diagnostic:
    source_range = diagnostic.source_range
    return types.Diagnostic(
        range=types.Range(
            start=types.Position(
                line=source_range.line,
                character=_utf16_character(
                    text,
                    source_range.line,
                    source_range.start_character,
                ),
            ),
            end=types.Position(
                line=source_range.line,
                character=_utf16_character(
                    text,
                    source_range.line,
                    source_range.end_character,
                ),
            ),
        ),
        message=diagnostic.message,
        severity=types.DiagnosticSeverity.Error,
        code=diagnostic.code,
        source="tanbun",
    )


def _utf16_character(text: str, line: int, character: int) -> int:
    lines = text.splitlines()
    line_text = lines[line] if line < len(lines) else ""
    prefix = line_text[:character]
    return len(prefix.encode("utf-16-le")) // 2


language_server = create_server()
