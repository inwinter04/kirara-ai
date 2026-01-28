import asyncio
import os
from typing import Any, Dict, List, Optional, Union

import jedi
from lsprotocol.types import (
    TEXT_DOCUMENT_CODE_ACTION,
    TEXT_DOCUMENT_COMPLETION,
    TEXT_DOCUMENT_DEFINITION,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    TEXT_DOCUMENT_DOCUMENT_SYMBOL,
    TEXT_DOCUMENT_HOVER,
    TEXT_DOCUMENT_SIGNATURE_HELP,
    WORKSPACE_DID_CHANGE_CONFIGURATION,
    CodeAction,
    CodeActionParams,
    CompletionItem,
    CompletionItemKind,
    CompletionList,
    CompletionOptions,
    CompletionParams,
    DefinitionParams,
    Diagnostic,
    DiagnosticSeverity,
    DidChangeConfigurationParams,
    DidChangeTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    DocumentSymbolParams,
    Hover,
    HoverParams,
    Location,
    MarkupContent,
    MarkupKind,
    MessageType,
    ParameterInformation,
    Position,
    Range,
    SignatureHelp,
    SignatureHelpOptions,
    SignatureHelpParams,
    SignatureInformation,
    SymbolInformation,
    SymbolKind,
    TextDocumentPositionParams,
)
from pygls.lsp.server import LanguageServer

from kirara_ai.logger import get_logger

from .diagnostics.base_diagnostic import BaseDiagnostic
from .diagnostics.import_check import ImportDiagnostic
from .diagnostics.jedi_syntax_check import JediSyntaxErrorDiagnostic
from .diagnostics.mandatory_function import MandatoryFunctionDiagnostic
from .diagnostics.pyflakes_check import PyflakesDiagnostic

logger = get_logger("LSP")


class QuartWsTransport(asyncio.Transport):
    def __init__(self, queue: asyncio.Queue):
        self._queue = queue

    def write(self, message: str):
        try:
            # put_nowait 通常是线程安全的
            self._queue.put_nowait(message)
        except Exception as e:
            logger.error(f"Error putting message into queue: {e}", exc_info=True)

    def close(self):
        self._queue.put_nowait(None)


class PythonLanguageServer(LanguageServer):
    mandatory_function_checker: Optional[MandatoryFunctionDiagnostic] = None

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        super().__init__("kirara-code-block-lsp", "v0.1", loop=loop)
        self._max_workers = 1
        self.diagnostic_checkers: List[BaseDiagnostic] = []
        self.diagnostic_checkers.append(ImportDiagnostic(self))
        self.diagnostic_checkers.append(JediSyntaxErrorDiagnostic(self))
        self.diagnostic_checkers.append(PyflakesDiagnostic(self))

        logger.info(
            f"Enabled diagnostic checkers: {[c.SOURCE_NAME for c in self.diagnostic_checkers]}"
        )
        self._setup_handlers()

    def configure_mandatory_function_checker(self, config: Dict[str, Any]) -> None:
        """配置必要函数检查器

        Args:
            config: 包含必要函数检查配置的字典
        """
        try:
            if self.mandatory_function_checker is None:
                self.mandatory_function_checker = MandatoryFunctionDiagnostic(
                    self, config
                )
                self.diagnostic_checkers.append(self.mandatory_function_checker)
                logger.info(
                    "MandatoryFunctionDiagnostic enabled with client configuration."
                )
            else:
                # 更新现有检查器的配置
                self.mandatory_function_checker.update_config(config)
                logger.info("MandatoryFunctionDiagnostic configuration updated.")

            # 记录配置详情
            logger.debug(f"MandatoryFunctionDiagnostic configured with: {config}")

            # 更新已启用的诊断检查器列表日志
            logger.info(
                f"Currently enabled diagnostic checkers: {[c.SOURCE_NAME for c in self.diagnostic_checkers]}"
            )
        except Exception as e:
            logger.error(
                f"Error configuring MandatoryFunctionDiagnostic: {str(e)}",
                exc_info=True,
            )
            self.show_message(
                f"Error configuring mandatory function checker: {str(e)}",
                MessageType.Error,
            )

    def _setup_handlers(self):
        """设置 LSP 方法处理程序"""
        logger.debug("Setting up LSP handlers...")

        @self.feature(
            TEXT_DOCUMENT_COMPLETION,
            CompletionOptions(trigger_characters=[".", "(", ",", "=", "\\", "[", "'"]),
        )
        @self.thread()
        def completions(ls, params: CompletionParams) -> CompletionList:
            """处理代码补全请求"""
            return self._get_completions(params)

        @self.feature(TEXT_DOCUMENT_HOVER)
        @self.thread()
        def hover(ls, params: HoverParams) -> Optional[Hover]:
            """处理悬停请求"""
            return self._get_hover(params)

        @self.feature(
            TEXT_DOCUMENT_SIGNATURE_HELP,
            SignatureHelpOptions(trigger_characters=["(", ",", "."]),
        )
        @self.thread()
        def signature(ls, params: SignatureHelpParams) -> Optional[SignatureHelp]:
            """处理函数签名帮助请求"""
            return self._get_signature_help(params)

        @self.feature(TEXT_DOCUMENT_DEFINITION)
        @self.thread()
        def definition(
            ls, params: DefinitionParams
        ) -> Optional[Union[Location, List[Location]]]:
            """处理跳转到定义请求"""
            return self._get_definition(params)

        @self.feature(TEXT_DOCUMENT_DOCUMENT_SYMBOL)
        @self.thread()
        def symbols(
            ls, params: DocumentSymbolParams
        ) -> Optional[List[SymbolInformation]]:
            """处理文档符号请求"""
            return self._get_document_symbols(params)

        @self.feature(TEXT_DOCUMENT_DID_OPEN)
        @self.thread()
        def did_open(ls, params: DidOpenTextDocumentParams):
            """文档打开时触发诊断"""
            logger.info(f"Document opened: {params.text_document.uri}")
            doc = ls.workspace.get_text_document(params.text_document.uri)
            if doc and doc.source != params.text_document.text:
                ls.workspace.put_document(params.text_document)

            self._publish_diagnostics(ls, params.text_document.uri)

        @self.feature(TEXT_DOCUMENT_DID_CHANGE)
        @self.thread()
        def did_change(ls, params: DidChangeTextDocumentParams):
            """文档更改时触发诊断"""
            self._publish_diagnostics(ls, params.text_document.uri)

        @self.feature(TEXT_DOCUMENT_DID_SAVE)
        @self.thread()
        def did_save(ls, params: DidSaveTextDocumentParams):
            """文档保存时触发诊断"""
            self._publish_diagnostics(ls, params.text_document.uri)

        @self.feature(TEXT_DOCUMENT_CODE_ACTION)
        @self.thread()
        def code_action(ls, params: CodeActionParams) -> Optional[List[CodeAction]]:
            """处理代码操作请求，提供快速修复建议"""
            return self._get_code_actions(params)

        @self.feature(WORKSPACE_DID_CHANGE_CONFIGURATION)
        @self.thread()
        def did_change_configuration(ls, params: DidChangeConfigurationParams):
            """处理客户端配置变更"""
            try:
                settings = params.settings
                if not settings:
                    return

                # 检查是否包含强制函数配置
                if "mandatoryFunction" in settings:
                    logger.info("Update mandatory function checker configuration")
                    self.configure_mandatory_function_checker(
                        settings["mandatoryFunction"]
                    )
                else:
                    logger.debug("No mandatory function configuration found")
            except Exception as e:
                logger.error(
                    f"Error processing configuration change: {str(e)}", exc_info=True
                )
                self.show_message(
                    f"Error processing configuration change: {str(e)}",
                    MessageType.Error,
                )

        logger.debug("LSP handlers set up.")

    def _get_script(
        self,
        params: Union[
            TextDocumentPositionParams,
            CompletionParams,
            HoverParams,
            SignatureHelpParams,
            DefinitionParams,
        ],
    ) -> Optional[jedi.Script]:
        """从参数中获取 jedi.Script 对象"""
        try:
            doc_uri = params.text_document.uri
            document = self.workspace.get_text_document(doc_uri)
            if not document:
                logger.warning(f"文档未在工作区中找到: {doc_uri}")
                return None

            path = document.path
            source = document.source
            position = params.position
            line = position.line + 1
            column = position.character

            script = jedi.Script(
                code=source,
                path=path if path else None,
                project=jedi.Project(os.getcwd()),
            )
            return script
        except Exception as e:
            logger.error(f"Error getting jedi.Script: {str(e)}", exc_info=True)
            return None

    def _get_completions(self, params: CompletionParams) -> CompletionList:
        """获取代码补全建议"""
        items: List[CompletionItem] = []
        script = self._get_script(params)
        if not script:
            return CompletionList(is_incomplete=False, items=items)

        try:
            position = params.position
            line = position.line + 1
            column = position.character

            completions = script.complete(line, column, fuzzy=True)

            for completion in completions:
                # ignore hidden completions like __str__
                if completion.name.startswith("__"):
                    continue

                kind = self._map_completion_type(completion.type)
                item = CompletionItem(
                    label=completion.name,
                    kind=kind,
                    detail=completion.description,
                    insert_text=completion.name,
                )
                items.append(item)
        except jedi.InternalError as e:
            logger.warning(f"Jedi completion error: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error getting completions: {str(e)}", exc_info=True)

        return CompletionList(is_incomplete=False, items=items)

    def _get_hover(self, params: HoverParams) -> Optional[Hover]:
        """获取悬停信息"""
        script = self._get_script(params)
        if not script:
            return None

        try:
            position = params.position
            line = position.line + 1
            column = position.character

            hover_info_list = script.help(line, column)

            if hover_info_list:
                docs = []
                for info in hover_info_list:
                    signature = f"```python\n{info.description}\n```"
                    doc = info.docstring(raw=True, fast=False)
                    content = signature
                    if doc:
                        content += f"\n\n---\n\n{doc}"
                    docs.append(content)

                full_docstring = "\n\n".join(docs)

                if full_docstring:
                    contents = MarkupContent(
                        kind=MarkupKind.Markdown, value=full_docstring
                    )
                    return Hover(contents=contents)
        except jedi.InternalError as e:
            logger.warning(f"Jedi hover error: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error getting hover information: {str(e)}", exc_info=True)

        return None

    def _get_signature_help(
        self, params: SignatureHelpParams
    ) -> Optional[SignatureHelp]:
        """获取函数签名帮助"""
        script = self._get_script(params)
        if not script:
            return None

        try:
            position = params.position
            line = position.line + 1
            column = position.character

            signatures = script.get_signatures(line, column)

            if signatures:
                signature_infos = []
                for sig_index, sig in enumerate(signatures):
                    param_infos = []
                    for i, param in enumerate(sig.params):
                        param_doc = param.description
                        param_label = param.name
                        param_info = ParameterInformation(
                            label=param_label, documentation=param_doc
                        )
                        param_infos.append(param_info)

                    sig_label = sig.to_string()

                    sig_info = SignatureInformation(
                        label=sig_label,
                        documentation=sig.docstring(raw=True, fast=False),
                        parameters=param_infos,
                    )
                    signature_infos.append(sig_info)

                if not signature_infos:
                    return None

                active_signature_index = 0
                active_parameter_index = (
                    signatures[active_signature_index].index
                    if signatures
                    and signatures[active_signature_index].index is not None
                    else 0
                )

                return SignatureHelp(
                    signatures=signature_infos,
                    active_signature=active_signature_index,
                    active_parameter=active_parameter_index,
                )
        except jedi.InternalError as e:
            logger.warning(f"Jedi signature help error: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error getting function signature: {str(e)}", exc_info=True)

        return None

    def _get_definition(self, params: DefinitionParams) -> List[Location]:
        """获取跳转到定义位置"""
        locations: List[Location] = []
        script = self._get_script(params)
        if not script:
            return locations

        try:
            position = params.position
            line = position.line + 1
            column = position.character

            definitions = script.goto(
                line, column, follow_imports=True, follow_builtin_imports=True
            )

            for definition in definitions:
                if (
                    definition.module_path
                    and definition.line is not None
                    and definition.column is not None
                ):
                    start_pos = Position(
                        line=definition.line - 1, character=definition.column
                    )
                    end_pos = Position(
                        line=definition.line - 1,
                        character=definition.column + len(definition.name),
                    )

                    range_val = Range(start=start_pos, end=end_pos)
                    try:
                        from pathlib import Path

                        uri = Path(definition.module_path).as_uri()
                    except ImportError:
                        uri = f"file://{definition.module_path}"

                    locations.append(Location(uri=uri, range=range_val))
        except jedi.InternalError as e:
            logger.warning(f"Jedi definition lookup error: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error getting definition location: {str(e)}", exc_info=True)

        return locations

    def _get_document_symbols(
        self, params: DocumentSymbolParams
    ) -> List[SymbolInformation]:
        """获取文档中的符号信息 (扁平列表)"""
        symbols = []
        try:
            doc_uri = params.text_document.uri
            document = self.workspace.get_text_document(doc_uri)
            if not document:
                return []

            _script = jedi.Script(
                code=document.source,
                path=document.path if document.path else None,
                project=jedi.Project(os.getcwd()),
            )
            names = _script.get_names(
                all_scopes=True, definitions=True, references=False
            )

            for name in names:
                if name.line is not None and name.column is not None:
                    kind = self._map_symbol_type(name.type)
                    start_pos = Position(line=name.line - 1, character=name.column)
                    end_pos = Position(
                        line=name.line - 1, character=name.column + len(name.name)
                    )

                    container_name = None
                    try:
                        parent = name.parent()
                        if parent and parent.type != "module":
                            container_name = parent.name
                    except Exception:
                        pass

                    symbol = SymbolInformation(
                        name=name.name,
                        kind=kind,
                        location=Location(
                            uri=doc_uri, range=Range(start=start_pos, end=end_pos)
                        ),
                        container_name=container_name,
                    )
                    symbols.append(symbol)
        except jedi.InternalError as e:
            logger.warning(f"Jedi symbol lookup error: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error getting document symbols: {str(e)}", exc_info=True)

        return symbols

    def _map_completion_type(self, type_str: str) -> CompletionItemKind:
        """将 jedi 补全类型映射到 LSP 补全类型"""
        mapping = {
            "module": CompletionItemKind.Module,
            "class": CompletionItemKind.Class,
            "instance": CompletionItemKind.Variable,
            "function": CompletionItemKind.Function,
            "param": CompletionItemKind.Variable,
            "path": CompletionItemKind.File,
            "keyword": CompletionItemKind.Keyword,
            "property": CompletionItemKind.Property,
            "statement": CompletionItemKind.Variable,
            "import": CompletionItemKind.Module,
            "method": CompletionItemKind.Method,
            " M": CompletionItemKind.Method,
            " C": CompletionItemKind.Class,
            " F": CompletionItemKind.Function,
        }
        return mapping.get(type_str, CompletionItemKind.Text)

    def _map_symbol_type(self, type_str: str) -> SymbolKind:
        """将 jedi 符号类型映射到 LSP 符号类型"""
        mapping = {
            "module": SymbolKind.Module,
            "class": SymbolKind.Class,
            "instance": SymbolKind.Variable,
            "function": SymbolKind.Function,
            "param": SymbolKind.Variable,
            "path": SymbolKind.File,
            "keyword": SymbolKind.Variable,
            "property": SymbolKind.Property,
            "statement": SymbolKind.Variable,
            "import": SymbolKind.Module,
            "method": SymbolKind.Method,
            " M": SymbolKind.Method,
            " C": SymbolKind.Class,
            " F": SymbolKind.Function,
            "namespace": SymbolKind.Namespace,
        }
        return mapping.get(type_str, SymbolKind.Variable)

    def _publish_diagnostics(self, ls: LanguageServer, doc_uri: str):
        """运行所有检查并发布诊断信息"""
        all_diagnostics = []
        document = ls.workspace.get_document(doc_uri)
        if not document:
            logger.warning(f"无法发布诊断，未找到文档: {doc_uri}")
            ls.publish_diagnostics(doc_uri, [])
            return

        for checker in self.diagnostic_checkers:
            checker_name = checker.SOURCE_NAME
            try:
                checker_diagnostics = checker.check(document)
                if checker_diagnostics:
                    all_diagnostics.extend(checker_diagnostics)
            except Exception as e:
                logger.error(
                    f"Diagnostic checker '{checker_name}' error: {str(e)}",
                    exc_info=True,
                )
                all_diagnostics.append(
                    Diagnostic(
                        range=Range(
                            start=Position(line=0, character=0),
                            end=Position(line=0, character=1),
                        ),
                        message=f"Diagnostic checker '{checker_name}' error: {str(e)}",
                        severity=DiagnosticSeverity.Error,
                        source="lsp-internal",
                    )
                )

        ls.publish_diagnostics(doc_uri, all_diagnostics)

    def _get_code_actions(self, params: CodeActionParams) -> Optional[List[CodeAction]]:
        """根据请求的诊断信息生成代码操作"""
        actions = []
        doc_uri = params.text_document.uri
        document = self.workspace.get_document(doc_uri)
        if not document:
            return None

        context_diagnostics = params.context.diagnostics

        diagnostics_by_source: Dict[str, List[Diagnostic]] = {}
        for diag in context_diagnostics:
            if diag.source:
                if diag.source not in diagnostics_by_source:
                    diagnostics_by_source[diag.source] = []
                diagnostics_by_source[diag.source].append(diag)

        for checker in self.diagnostic_checkers:
            checker_name = checker.SOURCE_NAME
            relevant_diagnostics = diagnostics_by_source.get(checker_name, [])
            if relevant_diagnostics:
                try:
                    checker_actions = checker.get_code_actions(
                        params, relevant_diagnostics
                    )
                    if checker_actions:
                        actions.extend(checker_actions)
                except Exception as e:
                    logger.error(
                        f"Code action checker '{checker_name}' error: {str(e)}",
                        exc_info=True,
                    )

        return actions if actions else None
