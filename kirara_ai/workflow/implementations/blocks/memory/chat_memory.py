from typing import Annotated, Any, Dict, List, Optional

from kirara_ai.im.message import IMMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.logger import get_logger
from kirara_ai.memory.composes.base import ComposableMessageType
from kirara_ai.memory.memory_manager import MemoryManager
from kirara_ai.memory.registry import (
    ComposerRegistry,
    DecomposerRegistry,
    ScopeRegistry,
)
from kirara_ai.workflow.core.block import Block, Input, Output, ParamMeta


def scope_type_options_provider(
    container: DependencyContainer, block: Block
) -> List[str]:
    return ["global", "group", "member"]


def decomposer_name_options_provider(
    container: DependencyContainer, block: Block
) -> List[str]:
    return ["default", "multi_element"]


class ChatMemoryQuery(Block):
    name = "chat_memory_query"
    inputs = {
        "chat_sender": Input(
            "chat_sender", "聊天对象", ChatSender, "要查询记忆的聊天对象"
        )
    }
    outputs = {
        "memory_content": Output(
            "memory_content", "记忆内容", List[ComposableMessageType], "记忆内容"
        )
    }
    container: DependencyContainer

    def __init__(
        self,
        scope_type: Annotated[
            Optional[str],
            ParamMeta(
                label="级别",
                description="要查询记忆的级别，代表记忆可以被共享的粒度。（例如：member 级别下，同一群聊下不同用户的记忆互相隔离； group 级别下，同一群组内所有成员记忆共享，但不同群组之间记忆互相隔离）",
                options_provider=scope_type_options_provider,
            ),
        ],
        decomposer_name: Annotated[
            Optional[str],
            ParamMeta(
                label="解析器名称",
                description="要使用的解析器名称",
                options_provider=decomposer_name_options_provider,
            ),
        ] = "default",
        extra_identifier: Annotated[
            Optional[str],
            ParamMeta(
                label="额外隔离标识符",
                description="仅支持输入英文，可为空。对于同一用户，不同标识符之间的记忆互相隔离。可用于避免不同工作流之间记忆互相干扰。",
            ),
        ] = None,
    ):
        self.scope_type = scope_type
        self.decomposer_name: str = decomposer_name or "default"
        self.extra_identifier = extra_identifier

    def execute(self, chat_sender: ChatSender) -> Dict[str, Any]:
        self.memory_manager = self.container.resolve(MemoryManager)

        # 如果没有指定作用域类型，使用配置中的默认值
        if self.scope_type is None:
            self.scope_type = self.memory_manager.config.default_scope

        # 获取作用域实例
        scope_registry = self.container.resolve(ScopeRegistry)
        self.scope = scope_registry.get_scope(self.scope_type)

        # 获取解析器实例
        decomposer_registry = self.container.resolve(DecomposerRegistry)
        self.decomposer = decomposer_registry.get_decomposer(self.decomposer_name)

        entries = self.memory_manager.query(
            self.scope, chat_sender, self.extra_identifier
        )
        memory_content = self.decomposer.decompose(entries)
        return {"memory_content": memory_content}


class ChatMemoryStore(Block):
    name = "chat_memory_store"

    inputs = {
        "user_msg": Input("user_msg", "用户消息", IMMessage, "用户消息", nullable=True),
        "llm_resp": Input(
            "llm_resp", "LLM 回复", LLMChatResponse, "LLM 回复", nullable=True
        ),
        "middle_steps": Input(
            "middle_steps",
            "中间步骤消息",
            List[ComposableMessageType],
            "中间步骤消息",
            nullable=True,
        ),
    }
    outputs = {}
    container: DependencyContainer

    def __init__(
        self,
        scope_type: Annotated[
            Optional[str],
            ParamMeta(
                label="级别",
                description="要查询记忆的级别，代表记忆可以被共享的粒度。（例如：member 级别下，同一群聊下不同用户的记忆互相隔离； group 级别下，同一群组内所有成员记忆共享，但不同群组之间记忆互相隔离）",
                options_provider=scope_type_options_provider,
            ),
        ],
        extra_identifier: Annotated[
            Optional[str],
            ParamMeta(
                label="额外隔离标识符",
                description="仅支持输入英文，可为空。对于同一用户，不同标识符之间的记忆互相隔离。可用于避免不同工作流之间记忆互相干扰。",
            ),
        ] = None,
    ):
        self.scope_type = scope_type
        self.logger = get_logger("Block.ChatMemoryStore")
        self.extra_identifier = extra_identifier

    def execute(
        self,
        user_msg: Optional[IMMessage] = None,
        llm_resp: Optional[LLMChatResponse] = None,
        middle_steps: Optional[List[ComposableMessageType]] = None,
    ) -> Dict[str, Any]:
        self.memory_manager = self.container.resolve(MemoryManager)

        # 如果没有指定作用域类型，使用配置中的默认值
        if self.scope_type is None:
            self.scope_type = self.memory_manager.config.default_scope

        # 获取作用域实例
        scope_registry = self.container.resolve(ScopeRegistry)
        self.scope = scope_registry.get_scope(self.scope_type)

        # 获取组合器实例
        composer_registry = self.container.resolve(ComposerRegistry)
        self.composer = composer_registry.get_composer("default")

        # 存储用户消息和LLM响应
        if user_msg is None:
            composed_messages: List[ComposableMessageType] = []
        else:
            composed_messages = [user_msg]

        if middle_steps is not None:
            composed_messages.extend(middle_steps)

        if llm_resp is not None:
            if llm_resp.message:
                composed_messages.append(llm_resp.message)
        if not composed_messages:
            self.logger.warning("No messages to store")
            return {}
        memory_entries = self.composer.compose(
            user_msg.sender if user_msg else None, composed_messages
        )
        self.memory_manager.store(self.scope, memory_entries, self.extra_identifier)

        return {}
