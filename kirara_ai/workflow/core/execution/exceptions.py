from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from kirara_ai.workflow.core.block import Block


class BlockExecutionFailedException(Exception):
    """块执行失败异常

    包含详细的上下文信息，便于调试和错误追踪
    """

    def __init__(
        self,
        message: str,
        block_name: Optional[str] = None,
        block_type: Optional[str] = None,
        original_error: Optional[Exception] = None,
        is_retryable: bool = False,
    ):
        self.block_name = block_name
        self.block_type = block_type
        self.original_error = original_error
        self.is_retryable = is_retryable
        self.timestamp = datetime.now()

        # 构建友好的错误消息
        full_message = message
        if block_name:
            full_message = f"[Block: {block_name}] {message}"
        if block_type:
            full_message = f"{full_message} (Type: {block_type})"

        super().__init__(full_message)


class WorkflowExecutionTimeoutException(Exception):
    """工作流执行超时异常"""

    def __init__(self, message: str, timeout_seconds: Optional[float] = None):
        self.timeout_seconds = timeout_seconds
        self.timestamp = datetime.now()

        full_message = message
        if timeout_seconds is not None:
            full_message = f"{message} (timeout: {timeout_seconds}s)"

        super().__init__(full_message)
