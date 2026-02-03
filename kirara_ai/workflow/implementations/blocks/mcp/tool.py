from base64 import b64decode
import httpx
import re
from typing import Annotated, Any, Dict, List

from mcp import types

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format import tool
from kirara_ai.llm.format.message import LLMToolResultContent
from kirara_ai.llm.format.tool import CallableWrapper, Tool, ToolCall
from kirara_ai.logger import get_logger
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.media.manager import MediaManager
from kirara_ai.media.types.media_type import MediaType
from kirara_ai.workflow.core.block import Block, Output
from kirara_ai.workflow.core.block.param import ParamMeta


def get_enabled_mcp_tools(container: DependencyContainer, block: Block) -> List[str]:
    mcp_manager = container.resolve(MCPServerManager)
    return list(mcp_manager.get_tools().keys())


class MCPToolProvider(Block):
    """
    提供MCP工具调用工具

    """

    name = "mcp_tool_provider"
    outputs = {"tools": Output("tools", "工具列表", List[Tool], "工具列表")}
    container: DependencyContainer

    def __init__(
        self,
        enabled_tools: Annotated[
            List[str],
            ParamMeta(
                label="启用工具列表",
                description="启用工具列表",
                options_provider=get_enabled_mcp_tools,
            ),
        ],
    ):
        self.logger = get_logger("MCPCallTool")
        self.enabled_tools = enabled_tools

    async def _call_tool(self, tool_call: ToolCall) -> LLMToolResultContent:
        """提供MCP工具调用执行回调"""
        self.logger.info(f"[MCP] 调用工具: {tool_call.function.name}")

        mcp_manager = self.container.resolve(MCPServerManager)

        server_info = mcp_manager.get_tool_server(tool_call.function.name)
        if not server_info:
            self.logger.error(f"[MCP] 找不到工具: {tool_call.function.name}")
            raise ValueError(f"找不到工具: {tool_call.function.name}")

        server, original_name = server_info
        result = await server.call_tool(original_name, tool_call.function.arguments)

        tool_result = await self._create_tool_result(
            tool_call.id, tool_call.function.name, result.content
        )

        tool_result.isError = result.isError
        return tool_result

    def execute(self) -> Dict[str, Any]:
        """
        提供MCP工具列表

        Returns:
            包含工具列表的字典
        """
        self.logger.info(f"[MCP] 启用的工具: {self.enabled_tools}")

        mcp_manager = self.container.resolve(MCPServerManager)
        mcp_tools = mcp_manager.get_tools()

        built_tools = []
        for tool_name, tool_info in mcp_tools.items():
            if tool_name in self.enabled_tools:
                built_tools.append(
                    Tool(
                        name=tool_name,
                        parameters=tool_info.tool_info.inputSchema,
                        description=tool_info.tool_info.description or "",
                        invokeFunc=CallableWrapper(self._call_tool),
                    )
                )

        self.logger.info(f"[MCP] 构建了 {len(built_tools)} 个工具")
        return {"tools": built_tools}

    async def _create_tool_result(
        self,
        tool_id: str,
        tool_name: str,
        content: list[types.TextContent | types.ImageContent | types.EmbeddedResource],
    ) -> LLMToolResultContent:
        """创建工具调用结果"""
        converted_content: List[tool.TextContent | tool.MediaContent] = []

        # DEBUG: 记录工具返回的原始内容
        self.logger.debug(f"[MCP] 工具 {tool_name} 返回了 {len(content)} 个内容项")

        for item in content:
            if isinstance(item, types.TextContent):
                # 检测是否包含图片URL
                text = item.text

                # DEBUG: 记录文本内容的前200个字符
                self.logger.debug(
                    f"[MCP] TextContent内容: {text[:200] if len(text) > 200 else text}"
                )

                if "图片URL：" in text or "image URL:" in text.lower():
                    # 提取图片URL
                    url_match = re.search(r"https?://[^\s\n\)]+", text)
                    if url_match:
                        image_url = url_match.group(0)
                        self.logger.debug(f"[MCP] 检测到图片URL: {image_url}")

                        try:
                            # 下载图片
                            async with httpx.AsyncClient(timeout=30.0) as client:
                                response = await client.get(image_url)
                                response.raise_for_status()
                                data = response.content

                                # 注册到媒体管理器
                            from urllib.parse import urlparse

                            parsed_url = urlparse(image_url)
                            format = (
                                parsed_url.path.split(".")[-1]
                                if "." in parsed_url.path
                                else "png"
                            )

                            media_id = await self.container.resolve(
                                MediaManager
                            ).register_from_data(
                                data, format=format, media_type=MediaType.IMAGE
                            )

                            self.logger.info(
                                f"[MCP] 图片已生成并注册: media_id={media_id}"
                            )

                            converted_content.append(
                                tool.MediaContent(
                                    media_id=media_id,
                                    mime_type=f"image/{format}",
                                    data=data,
                                )
                            )
                        except Exception as e:
                            self.logger.error(f"[MCP] 图片处理失败: {e}")
                            # 降级：保留原始文本
                            converted_content.append(tool.TextContent(text=text))
                    else:
                        self.logger.warning(f"[MCP] 文本包含图片URL关键词但未找到URL")
                        converted_content.append(tool.TextContent(text=text))
                else:
                    # 普通文本
                    self.logger.debug(f"[MCP] 普通文本，长度: {len(text)}")
                    converted_content.append(tool.TextContent(text=text))
            elif isinstance(item, types.ImageContent):
                self.logger.debug(f"[MCP] ImageContent，MIME类型: {item.mimeType}")
                data = b64decode(item.data)
                media_type = MediaType.from_mime(item.mimeType)
                format = item.mimeType.split("/")[1]
                media_id = await self.container.resolve(
                    MediaManager
                ).register_from_data(data, format=format, media_type=media_type)
                converted_content.append(
                    tool.MediaContent(
                        media_id=media_id, mime_type=item.mimeType, data=data
                    )
                )

        result = LLMToolResultContent(
            id=tool_id, name=tool_name, content=converted_content
        )

        # DEBUG: 记录最终转换结果
        media_count = sum(
            1 for c in converted_content if isinstance(c, tool.MediaContent)
        )
        text_count = sum(
            1 for c in converted_content if isinstance(c, tool.TextContent)
        )
        self.logger.info(
            f"[MCP] 工具结果转换完成: {media_count} 个图片, {text_count} 个文本"
        )

        return result
