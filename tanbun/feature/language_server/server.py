"""pyglsとTanbun診断の接続層."""

from __future__ import annotations

import asyncio
from collections import Counter
from time import perf_counter

from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.workspace import PositionCodec, ServerTextPosition, ServerTextRange

from tanbun.feature.language_service import (
    DefinitionLocation,
    ReferenceKind,
    SourceDocument,
    complete_terms,
    find_definition,
    find_definitions,
    find_references,
    group_references,
)
from tanbun.feature.language_service.analysis import (
    DocumentAnalysis,
    analyze,
)
from tanbun.feature.parsing.issue import ParseIssue, ParseIssueSeverity, SourceRange

from .workspace import local_documents

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
            analysis = _publish(server, uri, document.source, version)
            _report_analysis(server, analysis, visible=False)
        finally:
            if pending.get(uri) is asyncio.current_task():
                pending.pop(uri, None)

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(
        ls: LanguageServer,
        params: types.DidOpenTextDocumentParams,
    ) -> None:
        cancel_pending(params.text_document.uri)
        _report_message(ls, "Tanbun LSP: 文書を解析中…", visible=True)
        analysis = _publish(
            ls,
            params.text_document.uri,
            params.text_document.text,
        )
        _report_analysis(ls, analysis, visible=True)

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
        _report_message(ls, "Tanbun LSP: 保存した文書を解析中…", visible=True)
        analysis = _publish(
            ls,
            uri,
            document.source,
            getattr(document, "version", None),
        )
        _report_analysis(ls, analysis, visible=True)

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    def did_close(
        ls: LanguageServer,
        params: types.DidCloseTextDocumentParams,
    ) -> None:
        cancel_pending(params.text_document.uri)
        _publish_diagnostics(ls, params.text_document.uri, [])

    _register_completion(server)
    _register_definition(server)
    _register_references(server)
    _register_status(server)
    return server


def _register_completion(server: LanguageServer) -> None:
    """文書内用語のcompletion handlerを登録する."""

    @server.feature(
        types.TEXT_DOCUMENT_COMPLETION,
        types.CompletionOptions(trigger_characters=["{", "`"]),
    )
    def completion(
        ls: LanguageServer,
        params: types.CompletionParams,
    ) -> types.CompletionList | None:
        document = ls.workspace.get_text_document(params.text_document.uri)
        offset = document.offset_at_position(params.position)
        result = complete_terms(document.source, offset)
        if result is None:
            return None

        edit_range = types.Range(
            start=document.client_position_at_offset(result.start_offset),
            end=document.client_position_at_offset(result.end_offset),
        )
        return types.CompletionList(
            is_incomplete=False,
            items=[
                types.CompletionItem(
                    label=symbol.label,
                    kind=types.CompletionItemKind.Reference,
                    detail=symbol.detail,
                    filter_text=symbol.label,
                    text_edit=types.TextEdit(
                        range=edit_range,
                        new_text=f"{symbol.label}{result.closing}",
                    ),
                )
                for symbol in result.symbols
            ],
        )


def _register_definition(server: LanguageServer) -> None:
    """文書内用語のdefinition handlerを登録する."""

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
    def definition(
        ls: LanguageServer,
        params: types.DefinitionParams,
    ) -> types.Location | list[types.Location] | None:
        started = perf_counter()
        document = ls.workspace.get_text_document(params.text_document.uri)
        offset = document.offset_at_position(params.position)
        current = SourceDocument(
            uri=params.text_document.uri,
            text=document.source,
        )
        local_range = find_definition(current.text, offset)
        if local_range is not None:
            locations = (DefinitionLocation(current.uri, local_range),)
            workspace_documents = ()
        else:
            workspace_documents = local_documents(
                ls,
                current_uri=params.text_document.uri,
            )
            locations = find_definitions(current, offset, workspace_documents)
        duration_ms = _elapsed_ms(started)
        if not locations:
            _report_message(
                ls,
                f"Tanbun LSP: 定義が見つかりません ({duration_ms:.1f} ms)",
                visible=True,
            )
            return None
        documents = {item.uri: item.text for item in workspace_documents}
        documents[current.uri] = current.text
        results = _to_lsp_locations(ls, locations, documents)
        _report_message(
            ls,
            f"Tanbun LSP: 定義を{len(results)}件検索 ({duration_ms:.1f} ms)",
            visible=True,
        )
        return results[0] if len(results) == 1 else results


def _register_references(server: LanguageServer) -> None:
    """文書内・workspace内のreferences handlerを登録する."""

    @server.feature(types.TEXT_DOCUMENT_REFERENCES)
    def references(
        ls: LanguageServer,
        params: types.ReferenceParams,
    ) -> list[types.Location]:
        started = perf_counter()
        document = ls.workspace.get_text_document(params.text_document.uri)
        current = SourceDocument(params.text_document.uri, document.source)
        workspace_documents = local_documents(
            ls,
            current_uri=params.text_document.uri,
        )
        locations = find_references(
            current,
            document.offset_at_position(params.position),
            workspace_documents,
            include_declaration=params.context.include_declaration,
        )
        groups = group_references(locations)
        locations = tuple(location for group in groups for location in group.locations)
        documents = {item.uri: item.text for item in workspace_documents}
        documents[current.uri] = current.text
        results = _to_lsp_locations(ls, locations, documents)
        counts = Counter(location.kind for location in locations)
        details = [
            f"埋め込み{counts[ReferenceKind.EMBEDDED_TERM]}件",
            f"引用用語{counts[ReferenceKind.QUOTERM]}件",
        ]
        if counts[ReferenceKind.DEFINITION]:
            details.append(f"定義{counts[ReferenceKind.DEFINITION]}件")
        _report_message(
            ls,
            (
                f"Tanbun LSP: 参照を{len(results)}件検索 "
                f"({', '.join(details)} · {_elapsed_ms(started):.1f} ms)"
            ),
            visible=True,
        )
        return results


def _to_lsp_locations(
    server: LanguageServer,
    locations,
    documents: dict[str, str],
) -> list[types.Location]:
    return [
        types.Location(
            uri=location.uri,
            range=_to_client_range(
                documents[location.uri],
                location.source_range,
                server.workspace.position_codec,
            ),
        )
        for location in locations
    ]


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000


def _register_status(server: LanguageServer) -> None:
    """クライアントにLSPの起動を知らせる."""

    @server.feature(types.INITIALIZED)
    def initialized(
        ls: LanguageServer,
        _params: types.InitializedParams,
    ) -> None:
        _report_message(ls, "Tanbun LSP: 起動しました", visible=True)


def _publish(
    server: LanguageServer,
    uri: str,
    text: str,
    version: int | None = None,
) -> DocumentAnalysis:
    analysis = analyze(text)
    _publish_diagnostics(
        server,
        uri,
        [
            _to_lsp_diagnostic(text, item, server.workspace.position_codec)
            for item in analysis.diagnostics
        ],
        version,
    )
    return analysis


def _report_analysis(
    server: LanguageServer,
    analysis: DocumentAnalysis,
    *,
    visible: bool,
) -> None:
    statistics = analysis.statistics
    values = [
        f"{statistics.duration_ms:.0f} ms",
        f"{statistics.line_count}行",
    ]
    if statistics.term_count is not None:
        values.append(f"{statistics.term_count}用語")
    if statistics.node_count is not None:
        values.append(f"{statistics.node_count}ノード")
    if statistics.relation_count is not None:
        values.append(f"{statistics.relation_count}関係")
    error_count = sum(
        item.severity is ParseIssueSeverity.ERROR for item in analysis.diagnostics
    )
    warning_count = len(analysis.diagnostics) - error_count
    if error_count:
        values.append(f"{error_count}エラー")
    if warning_count:
        values.append(f"{warning_count}警告")
    message = f"Tanbun LSP: 準備完了 ({' · '.join(values)})"
    _report_message(server, message, visible=visible)


def _report_message(
    server: LanguageServer,
    message: str,
    *,
    visible: bool,
) -> None:
    server.window_log_message(
        types.LogMessageParams(type=types.MessageType.Info, message=message),
    )
    if visible:
        server.window_show_message(
            types.ShowMessageParams(type=types.MessageType.Info, message=message),
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


def _to_lsp_diagnostic(
    text: str,
    diagnostic: ParseIssue,
    position_codec: PositionCodec,
) -> types.Diagnostic:
    return types.Diagnostic(
        range=_to_client_range(text, diagnostic.source_range, position_codec),
        message=diagnostic.display_message(),
        severity=(
            types.DiagnosticSeverity.Warning
            if diagnostic.severity is ParseIssueSeverity.WARNING
            else types.DiagnosticSeverity.Error
        ),
        source="tanbun",
    )


def _to_client_range(
    text: str,
    source_range: SourceRange,
    position_codec: PositionCodec,
) -> types.Range:
    """Python文字位置をクライアントと合意した単位へ変換する."""
    return position_codec.range_to_client_units(
        text.splitlines(keepends=True),
        ServerTextRange(
            start=ServerTextPosition(
                line=source_range.line,
                character=source_range.start_character,
            ),
            end=ServerTextPosition(
                line=source_range.line,
                character=source_range.end_character,
            ),
        ),
    )


language_server = create_server()
