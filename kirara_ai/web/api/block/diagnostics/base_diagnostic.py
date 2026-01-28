import ast
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from lsprotocol.types import (
    CodeAction,
    CodeActionParams,
    Diagnostic,
    DiagnosticSeverity,
    Position,
    Range,
)
from pygls.lsp.server import LanguageServer
from pygls.workspace import TextDocument

logger = logging.getLogger(__name__)


class BaseDiagnostic(ABC):
    """诊断检查器的抽象基类"""

    SOURCE_NAME: str = "base-diagnostic"  # 每个子类应覆盖此项

    def __init__(self, ls: LanguageServer):
        self.ls = ls

    @abstractmethod
    def check(self, doc: TextDocument) -> List[Diagnostic]:
        """
        对文档执行诊断检查。

        Args:
            doc: 要检查的文档对象。

        Returns:
            诊断信息列表。
        """

    def get_code_actions(
        self, params: CodeActionParams, relevant_diagnostics: List[Diagnostic]
    ) -> List[CodeAction]:
        """
        为相关的诊断信息生成代码操作（快速修复）。

        Args:
            params: 代码操作请求参数。
            relevant_diagnostics: 与此检查器相关的诊断信息列表。

        Returns:
            代码操作列表。
        """
        # 默认实现：不提供任何代码操作
        return []

    def _create_diagnostic(
        self,
        message: str,
        node: Optional[ast.AST],
        severity: DiagnosticSeverity,
        data: Optional[Dict] = None,
        range_override: Optional[Range] = None,
    ) -> Diagnostic:
        """
        辅助函数：根据 AST 节点或显式范围创建 Diagnostic 对象。
        """
        diag_range: Optional[Range] = None

        if range_override:
            diag_range = range_override
        elif node and isinstance(node, ast.stmt):
            try:
                start_line = node.lineno - 1
                start_col = node.col_offset
                # AST 节点通常有 end_lineno 和 end_col_offset (Python 3.8+)
                # end_lineno 是 1-based 的结束行号 (exclusive or inclusive depending on context, often exclusive)
                # end_col_offset 是 0-based 的结束列偏移
                end_line = getattr(node, "end_lineno", start_line + 1) - 1
                end_col = getattr(node, "end_col_offset", start_col + 1)

                # 确保范围有效
                end_line = max(start_line, end_line)
                if end_line == start_line:
                    end_col = max(start_col + 1, end_col)  # 至少标记一个字符

                diag_range = Range(
                    start=Position(line=start_line, character=start_col),
                    end=Position(line=end_line, character=end_col),
                )
            except AttributeError:
                logger.warning(f"AST 节点缺少位置信息: {type(node)}")
                # Fallback if location info is missing
                diag_range = Range(
                    start=Position(line=0, character=0),
                    end=Position(line=0, character=1),
                )

        else:
            # 如果没有节点或范围，默认标记文件开头
            diag_range = Range(
                start=Position(line=0, character=0), end=Position(line=0, character=1)
            )

        return Diagnostic(
            range=diag_range,
            message=message,
            severity=severity,
            source=self.SOURCE_NAME,
            data=data,
        )

    def _ast_node_to_string(self, node: Optional[ast.AST]) -> str:
        """
        将 AST 注解节点转换为字符串表示。
        (从原 LanguageServer 类移动)
        """
        if node is None:
            return ""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):  # Python 3.8+
            # Handle NoneType explicitly if needed
            if node.value is None:
                return "None"
            return str(node.value)
        # Python < 3.8 NameConstant handling might be needed if supporting older versions
        # if isinstance(node, ast.NameConstant):
        #     return str(node.value)
        if isinstance(node, ast.Attribute):
            value_str = self._ast_node_to_string(node.value)
            # Avoid adding '.' if value is empty (shouldn't happen often)
            return f"{value_str}.{node.attr}" if value_str else node.attr
        if isinstance(node, ast.Subscript):
            base = self._ast_node_to_string(node.value)
            slice_val = node.slice
            if isinstance(slice_val, ast.Tuple):
                slice_str = ", ".join(
                    [self._ast_node_to_string(entry) for entry in slice_val.elts]
                )
            else:
                slice_str = self._ast_node_to_string(slice_val)
            return f"{base}[{slice_str}]"
        if isinstance(node, ast.List):
            return (
                f"List[{self._ast_node_to_string(node.elts[0])}]"
                if node.elts
                else "List"
            )
        if isinstance(node, ast.Dict):
            return (
                f"Dict[{self._ast_node_to_string(node.keys[0])}, {self._ast_node_to_string(node.values[0])}]"
                if node.keys and node.values
                else "Dict"
            )
        if isinstance(node, ast.Set):
            return (
                f"Set[{self._ast_node_to_string(node.elts[0])}]" if node.elts else "Set"
            )
        if isinstance(node, ast.Tuple):
            elts = [self._ast_node_to_string(elt) for elt in node.elts]
            if not elts:
                return "Tuple"
            # Handle Tuple[int] vs Tuple[int, ...]
            if len(elts) == 1:
                # Check if it's intended as variable-length tuple hint e.g. Tuple[int, ...]
                # This requires checking the source code or making assumptions.
                # Simple approach: always assume single element means fixed tuple.
                # Or f"tuple[{elts[0]}]" for consistency?
                return f"Tuple[{elts[0]}]"
            # Or f"tuple[{', '.join(elts)}]"
            return f"Tuple[{', '.join(elts)}]"

        # Fallback using ast.unparse (Python 3.9+)
        try:
            import sys

            if sys.version_info >= (3, 9):
                return ast.unparse(node)
            # 如果 unparse 不可用，则为旧版本 Python 的基本回退
            if isinstance(node, ast.Expr):
                return self._ast_node_to_string(node.value)
            # 如果需要，添加更多回退
            logger.debug(f"无法将 AST 节点转换为字符串（unparse 不可用）：{type(node)}")
            return "UnsupportedType"
        except Exception as e:
            logger.debug(f"Error using ast.unparse: {e}")
        return "UnsupportedType"  # Final fallback
