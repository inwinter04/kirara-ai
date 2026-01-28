import logging
from typing import Any, List

from lsprotocol.types import (
    CodeAction,
    CodeActionKind,
    CodeActionParams,
    Diagnostic,
    DiagnosticSeverity,
    Position,
    Range,
    TextEdit,
    WorkspaceEdit,
)
from pyflakes import api as pyflakes_api
from pyflakes import messages as pyflakes_messages
from pyflakes import reporter as pyflakes_reporter
from pygls.lsp.server import LanguageServer
from pygls.workspace import TextDocument

from .base_diagnostic import BaseDiagnostic

logger = logging.getLogger(__name__)


# 自定义 Reporter 来收集 Pyflakes 的错误/警告并转换为 LSP Diagnostic
class _LspReporter(pyflakes_reporter.Reporter):
    def __init__(self, source_name: str):
        super().__init__(None, None)  # 不需要标准输出/错误流
        self.diagnostics: List[Diagnostic] = []
        self._source_name = source_name

    def unexpectedError(self, filename: str, msg: str):
        # Pyflakes 内部错误
        logger.error(f"Pyflakes unexpected error in {filename}: {msg}")
        diagnostic = Diagnostic(
            range=Range(
                start=Position(line=0, character=0), end=Position(line=0, character=1)
            ),
            message=f"Pyflakes internal error: {msg}",
            severity=DiagnosticSeverity.Warning,  # 标记为警告，因为是检查器本身的问题
            source=self._source_name,
            code="PyflakesInternalError",
        )
        self.diagnostics.append(diagnostic)

    def syntaxError(self, filename: str, msg: str, lineno: int, offset: int, text: str):
        # 处理 Pyflakes 报告的语法错误
        line = lineno - 1  # 转换为 0-based
        col = (
            offset - 1 if offset > 0 else 0
        )  # Pyflakes offset 是 1-based，LSP 是 0-based

        # 语法错误通常标记单个字符或到行尾，这里简单标记一个字符
        # 更精确的范围可能需要分析 text 或 msg，但通常语法错误点明确
        end_col = col + 1

        # 确保行列号不为负数
        line = max(0, line)
        col = max(0, col)
        end_col = max(col + 1, end_col)  # 确保结束至少在开始后

        diagnostic = Diagnostic(
            range=Range(
                start=Position(line=line, character=col),
                end=Position(line=line, character=end_col),
            ),
            message=f"Syntax Error: {msg}",  # 添加前缀以明确是语法错误
            severity=DiagnosticSeverity.Error,  # 语法错误是 Error
            source=self._source_name,
            code="PyflakesSyntaxError",  # 使用特定的代码
        )
        self.diagnostics.append(diagnostic)

    def flake(self, message: Any):
        # message 是一个 pyflakes.messages.* 的实例
        line = message.lineno - 1  # 转换为 0-based
        col = message.col  # 0-based 列偏移

        # 尝试获取更精确的结束列
        end_col = col + 1  # 默认标记一个字符
        message_code = message.__class__.__name__  # 获取消息类型作为 code

        try:
            # 对于特定类型的消息，尝试使用参数长度确定范围
            if isinstance(
                message,
                (
                    pyflakes_messages.UnusedImport,
                    pyflakes_messages.UndefinedName,
                    pyflakes_messages.UndefinedExport,
                    pyflakes_messages.UndefinedLocal,
                    pyflakes_messages.DuplicateArgument,
                    pyflakes_messages.RedefinedWhileUnused,
                    pyflakes_messages.UnusedVariable,
                ),
            ):
                # 这些消息的第一个参数通常是相关的名称
                if message.message_args:
                    name = message.message_args[0]
                    if isinstance(name, str):
                        end_col = col + len(name)
            elif isinstance(message, pyflakes_messages.ImportShadowedByLoopVar):
                if message.message_args:
                    name = message.message_args[0]  # 第一个参数是名称
                    if isinstance(name, str):
                        end_col = col + len(name)

            # 对于 'from module import *' used，标记 '*'
            elif isinstance(message, pyflakes_messages.ImportStarUsed):
                end_col = col + 1  # '*' 只有一个字符

            # 其他消息类型保持默认单字符范围

        except Exception as e:
            logger.warning(f"计算 Pyflakes 诊断范围时出错: {e}", exc_info=True)
            end_col = col + 1  # 出错时回退

        # 确定严重性
        severity = DiagnosticSeverity.Warning  # 默认为警告
        if isinstance(
            message,
            (
                pyflakes_messages.UndefinedName,
                pyflakes_messages.UndefinedExport,
                pyflakes_messages.UndefinedLocal,
                pyflakes_messages.DoctestSyntaxError,
                pyflakes_messages.ForwardAnnotationSyntaxError,
            ),
        ):
            severity = DiagnosticSeverity.Error
        elif "syntax" in message_code.lower() or "invalid" in message_code.lower():
            # 捕捉其他可能的语法相关错误消息类型
            severity = DiagnosticSeverity.Error

        # 创建诊断数据，包含消息类型，用于代码操作
        diag_data = {"pyflakes_code": message_code}

        diagnostic = Diagnostic(
            range=Range(
                start=Position(line=line, character=col),
                end=Position(line=line, character=end_col),
            ),
            message=message.message % message.message_args,  # 格式化消息
            severity=severity,
            source=self._source_name,
            code=message_code,  # 使用 Pyflakes 消息类名作为 code
            data=diag_data,  # 附加数据
        )
        self.diagnostics.append(diagnostic)


class PyflakesDiagnostic(BaseDiagnostic):
    """使用 Pyflakes 检查 Python 代码错误的诊断器 (增强版)"""

    SOURCE_NAME: str = "pyflakes"

    def __init__(self, ls: LanguageServer):
        super().__init__(ls)

    def check(self, doc: TextDocument) -> List[Diagnostic]:
        """
        对文档执行 Pyflakes 检查。
        """

        diagnostics = []
        source = doc.source
        # Pyflakes 需要一个文件名，即使是临时的
        path = doc.path or "untitled.py"

        try:
            reporter = _LspReporter(self.SOURCE_NAME)
            # 使用 check 函数运行检查
            pyflakes_api.check(source, path, reporter=reporter)
            diagnostics = reporter.diagnostics
        except Exception as e:
            logger.error(f"运行 Pyflakes 时发生内部错误: {str(e)}", exc_info=True)
            # 创建一个诊断信息报告 Pyflakes 本身的错误
            diagnostics.append(
                self._create_diagnostic(
                    message=f"运行 Pyflakes 时出错: {e}",
                    node=None,  # 没有关联的 AST 节点
                    severity=DiagnosticSeverity.Warning,  # 检查器问题标记为警告
                    range_override=Range(
                        start=Position(line=0, character=0),
                        end=Position(line=0, character=1),
                    ),
                )
            )

        return diagnostics

    def get_code_actions(
        self, params: CodeActionParams, relevant_diagnostics: List[Diagnostic]
    ) -> List[CodeAction]:
        """
        为 Pyflakes 诊断提供代码操作（快速修复）。
        目前主要实现 "移除未使用的导入"。
        """
        actions = []
        doc_uri = params.text_document.uri
        document = self.ls.workspace.get_text_document(doc_uri)
        if not document:
            logger.warning(f"无法获取 Pyflakes 代码操作，未找到文档: {doc_uri}")
            return []

        lines = document.source.splitlines(True)  # 保留换行符

        for diag in relevant_diagnostics:
            # 确保是来自 pyflakes 的诊断并且有附加数据
            if diag.source != self.SOURCE_NAME or not diag.data:
                continue

            pyflakes_code = diag.data.get("pyflakes_code")

            # --- 快速修复：移除未使用的导入 (UnusedImport) ---
            if pyflakes_code == "UnusedImport":
                # 诊断范围指向未使用的名称，我们需要删除包含它的整行（或部分行）
                # 简单起见，我们先实现删除整行。
                # 注意：如果一行导入多个，这会删除所有导入。更精细的处理需要 AST 分析。
                start_line = diag.range.start.line
                end_line = diag.range.end.line  # Pyflakes 通常在单行内报告

                if start_line < 0 or end_line >= len(lines):
                    logger.warning(
                        f"无效的 Pyflakes 诊断范围用于移除导入: {diag.range}"
                    )
                    continue

                # 定义要删除的范围：从该行开始到下一行开始（删除整行及换行符）
                delete_start_pos = Position(line=start_line, character=0)
                delete_end_line_exclusive = end_line + 1

                if delete_end_line_exclusive < len(lines):
                    # 删除到下一行的开头
                    delete_end_pos = Position(
                        line=delete_end_line_exclusive, character=0
                    )
                else:
                    # 如果是最后一行，删除到该行的末尾
                    delete_end_pos = Position(
                        line=end_line, character=len(lines[end_line])
                    )

                text_edit = TextEdit(
                    range=Range(start=delete_start_pos, end=delete_end_pos), new_text=""
                )
                edit = WorkspaceEdit(changes={doc_uri: [text_edit]})

                # 尝试从消息中提取模块/变量名以获得更好的标题
                title_suffix = ""
                try:
                    # 'imported but unused' or 'assigned to but never used'
                    parts = diag.message.split("'")
                    if len(parts) > 1:
                        title_suffix = f": '{parts[1]}'"
                except Exception:
                    pass  # 忽略提取错误

                action = CodeAction(
                    title=f"移除未使用的导入{title_suffix}",
                    kind=CodeActionKind.QuickFix,
                    diagnostics=[diag],  # 关联此代码操作到原始诊断
                    edit=edit,
                    is_preferred=True,  # 移除未使用导入通常是首选操作
                )
                actions.append(action)

            # --- 可以添加其他快速修复，例如：---
            # if pyflakes_code == "SomeOtherFixableIssue":
            #     # ... 实现对应的 TextEdit 和 CodeAction ...
            #     pass

        return actions
