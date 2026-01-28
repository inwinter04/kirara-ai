import ast
import importlib.util
import logging
import os
from typing import List, Optional, Tuple

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
from pygls.workspace import TextDocument

from .base_diagnostic import BaseDiagnostic

logger = logging.getLogger(__name__)


class ImportDiagnostic(BaseDiagnostic):
    """检查导入语句有效性的诊断器"""

    SOURCE_NAME: str = "import-check"

    def _get_package_context(
        self, path: Optional[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """获取文件路径对应的目录和包名"""
        if not path or not os.path.exists(path):
            return None, None
        file_dir = os.path.dirname(path)
        # Simple package detection: check for __init__.py or assume dir name is package
        # This might not be fully robust for complex project structures.
        package_name = None
        try:
            # Walk up to find a directory containing __init__.py or setup.py?
            # For simplicity, let's use the immediate parent directory name if it seems plausible
            potential_pkg_name = os.path.basename(file_dir)
            # Avoid using names like 'src', 'lib' directly unless structure confirms it
            # A better approach might involve analyzing project structure or sys.path
            # Let's assume basename is the package for resolve_name context
            package_name = potential_pkg_name or None

        except Exception:
            logger.warning(f"无法确定 '{path}' 的包上下文")

        return file_dir, package_name

    def check(self, doc: TextDocument) -> List[Diagnostic]:
        """检查导入语句是否有效"""
        diagnostics = []
        source = doc.source
        path = doc.path
        try:
            tree = ast.parse(source)
            file_dir, package_name = self._get_package_context(path)

            # Store found issues to avoid duplicate diagnostics for the same line/module
            reported_issues = set()  # Store (line_no, module_name_str) tuples

            for node in ast.walk(tree):
                module_name_str: Optional[str] = None
                # Record node for reporting and fixing
                import_node: Optional[ast.stmt] = None
                if not isinstance(node, ast.stmt):
                    continue
                line_no = node.lineno if hasattr(node, "lineno") else 0

                if isinstance(node, ast.Import):
                    import_node = node
                    for alias in node.names:
                        module_name_str = alias.name
                        issue_key = (line_no, module_name_str)
                        if issue_key in reported_issues:
                            continue

                        try:
                            spec = importlib.util.find_spec(module_name_str)
                            if spec is None:
                                message = f"无法找到模块 '{module_name_str}'"
                                diagnostic = self._create_diagnostic(
                                    message,
                                    import_node,
                                    DiagnosticSeverity.Error,
                                    data={"fix_type": "remove_import"},
                                )
                                diagnostics.append(diagnostic)
                                reported_issues.add(issue_key)
                        except ModuleNotFoundError:
                            message = f"无法找到模块 '{module_name_str}'"
                            diagnostic = self._create_diagnostic(
                                message,
                                import_node,
                                DiagnosticSeverity.Error,
                                data={"fix_type": "remove_import"},
                            )
                            diagnostics.append(diagnostic)
                            reported_issues.add(issue_key)
                        except (
                            Exception
                        ) as e:  # Catch other potential errors during find_spec
                            message = f"检查导入 '{module_name_str}' 时出错: {e}"
                            diagnostic = self._create_diagnostic(
                                message,
                                import_node,
                                DiagnosticSeverity.Warning,  # Warning for general errors
                                # Still offer removal
                                data={"fix_type": "remove_import"},
                            )
                            diagnostics.append(diagnostic)
                            reported_issues.add(issue_key)

                elif isinstance(node, ast.ImportFrom):
                    import_node = node
                    module_name_str = node.module  # Can be None for 'from . import ...'
                    level = node.level
                    is_relative = level > 0

                    # Construct the name being resolved for reporting/key
                    if is_relative:
                        relative_prefix = "." * level
                        resolving_name = f"{relative_prefix}{module_name_str or ''}"
                    else:
                        # Should have module name if not relative
                        resolving_name = module_name_str or ""

                    # Skip if nothing to resolve (e.g. invalid syntax?)
                    if not resolving_name:
                        continue

                    issue_key = (line_no, resolving_name)
                    if issue_key in reported_issues:
                        continue

                    resolved_spec = None
                    error_message = None
                    severity = DiagnosticSeverity.Error

                    try:
                        if is_relative:
                            if file_dir and package_name:
                                # Resolve the name relative to the current file's package context
                                resolved_name = importlib.util.resolve_name(
                                    resolving_name, package_name
                                )
                                resolved_spec = importlib.util.find_spec(resolved_name)
                                if resolved_spec is None:
                                    error_message = f"无法找到相对导入的模块 '{resolving_name}' (解析为 '{resolved_name}' 来自 '{package_name}')"
                            else:
                                # Cannot reliably check relative imports without path/package context
                                error_message = f"无法可靠地检查相对导入 '{resolving_name}' (缺少文件路径或包上下文)"
                                severity = (
                                    DiagnosticSeverity.Warning
                                )  # Downgrade severity if unsure
                        elif module_name_str:  # Absolute import
                            resolved_spec = importlib.util.find_spec(module_name_str)
                            if resolved_spec is None:
                                error_message = f"无法找到模块 '{module_name_str}'"

                    except (ImportError, ValueError) as e:
                        error_message = f"无法解析或找到导入 '{resolving_name}': {e}"
                    except Exception as e:
                        error_message = (
                            f"检查导入 '{resolving_name}' 时发生意外错误: {e}"
                        )
                        severity = DiagnosticSeverity.Warning

                    # Create diagnostic if an error occurred
                    if error_message:
                        diagnostic = self._create_diagnostic(
                            error_message,
                            import_node,
                            severity,
                            data={"fix_type": "remove_import"},
                        )
                        diagnostics.append(diagnostic)
                        reported_issues.add(issue_key)

        except SyntaxError:
            # Syntax errors handled elsewhere
            logger.debug("跳过导入检查，存在语法错误")
        except Exception as e:
            logger.error(f"检查导入时发生内部错误: {str(e)}", exc_info=True)

        return diagnostics

    def get_code_actions(
        self, params: CodeActionParams, relevant_diagnostics: List[Diagnostic]
    ) -> List[CodeAction]:
        """提供删除无效导入的代码操作"""
        actions = []
        doc_uri = params.text_document.uri
        document = self.ls.workspace.get_text_document(doc_uri)
        if not document:
            return []

        lines = document.source.splitlines(True)  # Keep line endings

        for diag in relevant_diagnostics:
            if diag.source != self.SOURCE_NAME:
                continue

            fix_type = diag.data.get("fix_type") if diag.data else None

            if fix_type == "remove_import":
                # The diagnostic range should cover the import statement node
                start_line = diag.range.start.line
                end_line = diag.range.end.line  # The line index where the node ends

                # Ensure line numbers are valid
                if start_line < 0 or end_line >= len(lines):
                    logger.warning(f"无效的诊断范围用于删除导入: {diag.range}")
                    continue

                # Define the range to be deleted: the entire line(s) of the import statement
                delete_start_pos = Position(line=start_line, character=0)

                # Determine the end position to include the newline of the last line involved
                # Check if the node ends exactly at the end of a line (excluding newline)
                # This requires knowing the exact end column from AST, which might be tricky.
                # Safer approach: Delete up to the start of the *next* line.
                delete_end_line_exclusive = end_line + 1
                if delete_end_line_exclusive < len(lines):
                    # Delete up to the start of the next line
                    delete_end_pos = Position(
                        line=delete_end_line_exclusive, character=0
                    )
                else:
                    # Delete to the end of the last line of the file
                    delete_end_pos = Position(
                        line=end_line, character=len(lines[end_line])
                    )

                # Create the TextEdit to remove the line(s)
                text_edit = TextEdit(
                    range=Range(start=delete_start_pos, end=delete_end_pos), new_text=""
                )

                # Extract module name from message for a better title if possible
                module_name_match = diag.message.split("'")
                title_suffix = (
                    f": {module_name_match[1]}" if len(module_name_match) > 1 else ""
                )
                title = f"移除无效的导入语句{title_suffix}"

                edit = WorkspaceEdit(changes={doc_uri: [text_edit]})

                action = CodeAction(
                    title=title,
                    kind=CodeActionKind.QuickFix,
                    diagnostics=[diag],
                    edit=edit,
                    is_preferred=False,  # Deleting code might not always be preferred
                )
                actions.append(action)

        return actions
