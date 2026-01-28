import logging
from typing import List

import jedi
from lsprotocol.types import Diagnostic, DiagnosticSeverity, Position, Range
from pygls.lsp.server import LanguageServer
from pygls.workspace import TextDocument

from .base_diagnostic import BaseDiagnostic

logger = logging.getLogger(__name__)


class JediSyntaxErrorDiagnostic(BaseDiagnostic):
    """使用 Jedi 检查 Python 语法错误的诊断器"""

    SOURCE_NAME: str = "syntax-error"

    def __init__(self, ls: LanguageServer):
        super().__init__(ls)

    def check(self, doc: TextDocument) -> List[Diagnostic]:
        """
        对文档执行语法错误检查。

        Args:
            doc: 要检查的文档对象。

        Returns:
            诊断信息列表。
        """
        diagnostics = []
        source = doc.source
        path = doc.path

        try:
            # 使用 Jedi 创建 Script 对象
            # 注意：即使代码有语法错误，Jedi 通常也能创建 Script 对象
            script = jedi.Script(code=source, path=path or None)

            # 获取语法错误
            syntax_errors = script.get_syntax_errors()

            for error in syntax_errors:
                # Jedi 的行列号是 1-based，LSP 是 0-based
                start_line = error.line - 1
                start_char = error.column
                # Jedi 的 until_line/until_column 定义了错误范围的结束（通常是独占的）
                # LSP 的 Range 结束位置也是独占的
                end_line = error.until_line - 1
                end_char = error.until_column

                # 创建 LSP Range 对象
                # 确保行列号不为负数
                start_line = max(0, start_line)
                start_char = max(0, start_char)
                end_line = max(start_line, end_line)  # 结束行不能在开始行之前
                if end_line == start_line:
                    end_char = max(
                        start_char + 1, end_char
                    )  # 结束列至少在开始列之后一个字符

                error_range = Range(
                    start=Position(line=start_line, character=start_char),
                    end=Position(line=end_line, character=end_char),
                )

                # 使用 _create_diagnostic 辅助函数创建诊断信息
                diagnostic = self._create_diagnostic(
                    message=error.get_message(),
                    node=None,
                    severity=DiagnosticSeverity.Error,
                    range_override=error_range,  # 使用 Jedi 提供的范围
                )
                diagnostics.append(diagnostic)

        except Exception as e:
            # 捕获 Jedi 或其他意外错误
            logger.error(f"检查语法错误时发生内部错误: {str(e)}", exc_info=True)
            # 可以选择性地添加一个通用的错误诊断
            diagnostics.append(
                self._create_diagnostic(
                    message=f"检查语法错误时出错: {e}",
                    node=None,
                    severity=DiagnosticSeverity.Warning,  # 使用警告级别，因为这是检查器本身的问题
                    range_override=Range(
                        start=Position(line=0, character=0),
                        end=Position(line=0, character=1),
                    ),
                )
            )

        return diagnostics
