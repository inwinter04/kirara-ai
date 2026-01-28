import ast
import logging
from typing import Any, Dict, List, Optional

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
from pygls.lsp.server import LanguageServer
from pygls.workspace import TextDocument

from .base_diagnostic import BaseDiagnostic

logger = logging.getLogger(__name__)


class MandatoryFunctionDiagnostic(BaseDiagnostic):
    """检查强制函数声明的诊断器"""

    SOURCE_NAME: str = "mandatory-function-check"

    config: Optional[Dict[str, Any]] = None
    config_name: Optional[str] = None
    config_params: Optional[List[Dict[str, Any]]] = None
    config_return: Optional[str] = None
    param_signatures: Optional[List[str]] = None
    expected_signature_str: Optional[str] = None
    expected_signature_data: Optional[Dict[str, Any]] = None

    def __init__(self, ls: LanguageServer, config: Optional[Dict[str, Any]]):
        super().__init__(ls)
        self.config = None
        self.config_name = None
        self.config_params = None
        self.config_return = None
        self.param_signatures = None
        self.expected_signature_str = None
        self.expected_signature_data = None

        # 初始化配置
        if config:
            self.update_config(config)

    def update_config(self, config: Dict[str, Any]) -> None:
        """更新诊断器配置

        Args:
            config: 包含必要函数检查配置的字典
        """
        try:
            self.config = config
            assert self.config is not None
            self.config_name = self.config["name"]
            self.config_params = self.config["params"]
            assert self.config_params is not None
            self.config_return = self.config["return_type"]
            self.param_signatures = [
                f"{p['name']}: {p['type_hint']}" for p in self.config_params
            ]
            self.expected_signature_str = f"def {self.config_name}({', '.join(self.param_signatures)}) -> {self.config_return}"
            self.expected_signature_data = {
                "name": self.config_name,
                "params": self.param_signatures,
                "return": self.config_return,
            }
            logger.info(f"Updated mandatory function config: {self.config_name}")
            logger.debug(f"Expected signature: {self.expected_signature_str}")
        except KeyError as e:
            logger.error(f"Invalid mandatory function config, missing key: {e}")
            self.config = None  # 配置无效时禁用检查器
        except Exception as e:
            logger.error(f"Error updating mandatory function config: {e}")
            self.config = None

    def check(self, doc: TextDocument) -> List[Diagnostic]:
        """检查源代码是否包含配置的强制函数声明"""
        diagnostics: List[Diagnostic] = []
        if not self.config or not self.config_params:
            return diagnostics

        source = doc.source
        found_match = False
        potential_match_node = None
        # Store reasons for mismatch if name matches
        mismatch_reasons: List[str] = []

        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == self.config_name:
                    potential_match_node = node
                    # Reasons for this specific node
                    current_reasons: List[str] = []

                    # 1. Check parameter count
                    num_actual_params = len(node.args.args)
                    if num_actual_params != len(self.config_params):
                        current_reasons.append(
                            f"参数数量不匹配: 期望 {len(self.config_params)} 个, 实际 {num_actual_params} 个"
                        )

                    # 2. Check parameter names and types (only if count matches for clearer messages)
                    params_match = True
                    if num_actual_params == len(self.config_params):
                        for i, arg_node in enumerate(node.args.args):
                            config_param = self.config_params[i]
                            arg_name = arg_node.arg
                            arg_type_str = self._ast_node_to_string(arg_node.annotation)
                            config_type_hint = config_param.get("type_hint", "")

                            # Normalize empty/Any types for comparison
                            norm_arg_type = arg_type_str or "Any"
                            norm_config_type = config_type_hint or "Any"

                            types_match = norm_arg_type == norm_config_type

                            if arg_name != config_param["name"]:
                                params_match = False
                                current_reasons.append(
                                    f"第 {i + 1} 个参数名不匹配: 期望 '{config_param['name']}', 实际 '{arg_name}'"
                                )
                            elif (
                                not types_match and config_type_hint
                            ):  # Only check type if config specifies one
                                params_match = False
                                current_reasons.append(
                                    f"参数 '{arg_name}' 类型不匹配: 期望 '{config_type_hint}', 实际 '{arg_type_str or '无类型'}'"
                                )
                    # If count didn't match, mark params as mismatch
                    elif num_actual_params != len(self.config_params):
                        params_match = False

                    # 3. Check return type
                    return_type_str = self._ast_node_to_string(node.returns)
                    config_return_type = self.config_return or ""

                    norm_return_type = return_type_str or "Any"
                    norm_config_return = config_return_type or "Any"

                    return_match = norm_return_type == norm_config_return

                    if (
                        not return_match and config_return_type
                    ):  # Only check if config specifies return
                        current_reasons.append(
                            f"返回类型不匹配: 期望 '{config_return_type}', 实际 '{return_type_str or '无类型'}'"
                        )

                    # If all checks pass for this node
                    if not current_reasons:
                        found_match = True
                        break  # Found a perfect match, stop searching
                    else:
                        # Store reasons from the first encountered mismatching function
                        if not mismatch_reasons:
                            mismatch_reasons = current_reasons

            # --- End of AST walk ---

        except SyntaxError as e:
            # Let other checkers handle syntax errors
            return []
        except Exception as e:
            logger.error(f"Error checking mandatory function: {str(e)}", exc_info=True)
            return []

        # --- Create Diagnostic if needed ---
        if not found_match:
            if potential_match_node:
                # Found function with same name but wrong signature
                param_signatures_actual = []
                for arg in potential_match_node.args.args:
                    sig = arg.arg
                    annotation = self._ast_node_to_string(arg.annotation)
                    if annotation:
                        sig += f": {annotation}"
                    param_signatures_actual.append(sig)

                return_actual = self._ast_node_to_string(potential_match_node.returns)
                actual_signature_str = f"def {potential_match_node.name}({', '.join(param_signatures_actual)})"
                if return_actual:
                    actual_signature_str += f" -> {return_actual}"

                mismatch_hint = ""
                if mismatch_reasons:
                    mismatch_hint = "\n具体差异:\n- " + "\n- ".join(mismatch_reasons)

                message = f"函数 '{self.config_name}' 的签名与强制要求不符。\n期望: {self.expected_signature_str}\n实际: {actual_signature_str}{mismatch_hint}"

                diagnostic = self._create_diagnostic(
                    message=message,
                    node=potential_match_node,
                    severity=DiagnosticSeverity.Error,
                    data={
                        "expected_signature": self.expected_signature_data,
                        "fix_type": "replace_signature",
                    },  # Keep replace type, even if not implemented yet
                )
                diagnostics.append(diagnostic)
            else:
                # Did not find the function at all
                lines = source.splitlines()
                # Position after last line for insertion
                file_end_line = len(lines)
                file_end_col = 0
                message = f"缺少强制函数声明: '{self.config_name}'。\n期望签名: {self.expected_signature_str}"
                diagnostic = self._create_diagnostic(
                    message=message,
                    node=None,  # No specific node
                    severity=DiagnosticSeverity.Error,
                    # Mark the end of the file for insertion
                    range_override=Range(
                        start=Position(line=file_end_line, character=file_end_col),
                        end=Position(line=file_end_line, character=file_end_col),
                    ),
                    data={
                        "expected_signature": self.expected_signature_data,
                        "fix_type": "insert_function",
                    },
                )
                diagnostics.append(diagnostic)

        return diagnostics

    def get_code_actions(
        self, params: CodeActionParams, relevant_diagnostics: List[Diagnostic]
    ) -> List[CodeAction]:
        """为强制函数错误提供代码操作"""
        actions: List[CodeAction] = []
        doc_uri = params.text_document.uri
        document = self.ls.workspace.get_text_document(doc_uri)
        if not document or not self.config:  # Need document and config for actions
            return []

        for diag in relevant_diagnostics:
            # Ensure the diagnostic came from this checker
            if diag.source != self.SOURCE_NAME:
                continue

            fix_type = diag.data.get("fix_type") if diag.data else None
            expected_sig_data = (
                diag.data.get("expected_signature") if diag.data else None
            )

            # Double check data matches current config in case config changed
            if not expected_sig_data or expected_sig_data["name"] != self.config_name:
                continue

            if fix_type == "insert_function":
                title = f"生成强制函数 '{self.config_name}'"
                param_str = ", ".join(expected_sig_data["params"])
                return_str = (
                    f" -> {expected_sig_data['return']}"
                    if expected_sig_data["return"]
                    else ""
                )
                # Add two newlines before the function if the file is not empty
                prefix = "\n\n" if document.source.strip() else ""
                # Add a basic docstring and pass
                new_text = f'{prefix}def {self.config_name}({param_str}){return_str}:\n    """强制函数存根"""\n    pass\n'

                # Use the range from the diagnostic (end of file)
                insert_pos = diag.range.start
                edit = WorkspaceEdit(
                    changes={
                        doc_uri: [
                            TextEdit(
                                range=Range(start=insert_pos, end=insert_pos),
                                new_text=new_text,
                            )
                        ]
                    }
                )
                action = CodeAction(
                    title=title,
                    kind=CodeActionKind.QuickFix,
                    diagnostics=[diag],
                    edit=edit,
                    is_preferred=True,  # Make it the default action if possible
                )
                actions.append(action)

        return actions
