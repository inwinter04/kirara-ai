#!/usr/bin/env python3
"""
独立的智谱图像生成MCP服务器
添加了详细的错误处理和日志
支持从配置文件读取API密钥
"""

import asyncio
import base64
import os
import sys
import traceback
from pathlib import Path

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# 强制输出到stderr，这样可以看到启动日志
def log(message):
    """输出日志到stderr"""
    print(message, file=sys.stderr, flush=True)


def get_api_key_from_config():
    """
    从配置文件读取API密钥
    优先级：环境变量 > 配置文件 > 命令行参数
    """
    # 1. 首先检查环境变量
    api_key = os.getenv("ZHIPU_API_KEY", "")
    if api_key:
        log("[MCP] 使用环境变量中的API密钥")
        return api_key

    # 2. 尝试从配置文件读取
    config_paths = [
        Path(project_root) / "data" / "config.yaml",
        Path(project_root) / "config.yaml",
        Path.cwd() / "data" / "config.yaml",
        Path.cwd() / "config.yaml",
    ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                import yaml

                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)

                # 从MCP服务器配置中查找API密钥
                if "mcp" in config and "servers" in config["mcp"]:
                    for server in config["mcp"]["servers"]:
                        if server.get("id") == "zhipu-image-generator":
                            env = server.get("env", {})
                            if "ZHIPU_API_KEY" in env:
                                log(f"[MCP] 从配置文件读取API密钥: {config_path}")
                                return env["ZHIPU_API_KEY"]

                # 尝试从LLM后端配置中查找智谱API密钥
                if "llms" in config and "api_backends" in config["llms"]:
                    for backend in config["llms"]["api_backends"]:
                        if backend.get("adapter") == "ZhipuAI":
                            config_api_key = backend.get("config", {}).get(
                                "api_key", ""
                            )
                            if config_api_key:
                                log(f"[MCP] 从LLM后端配置读取API密钥: {config_path}")
                                return config_api_key

            except Exception as e:
                log(f"[MCP] 读取配置文件失败 {config_path}: {e}")

    # 3. 未找到API密钥
    return ""


log("[MCP] ========================================")
log("[MCP] 智谱图像生成MCP服务器启动中...")
log(f"[MCP] Python版本: {sys.version}")
log(f"[MCP] Python路径: {sys.executable}")
log(f"[MCP] 工作目录: {os.getcwd()}")
log(f"[MCP] 脚本路径: {__file__}")
log(f"[MCP] 项目根目录: {project_root}")

# 获取API密钥
api_key = get_api_key_from_config()
log(f"[MCP] API密钥状态: {'已设置' if api_key else '未设置'}")

if api_key:
    log(f"[MCP] API密钥预览: {api_key[:10]}...{api_key[-4:]}")

# 检查依赖
log("[MCP] 检查依赖...")
missing_deps = []

try:
    import httpx

    log("[MCP] ✅ httpx 已安装")
except ImportError:
    missing_deps.append("httpx")
    log("[MCP] ⚠️ httpx 未安装")

try:
    import yaml

    log("[MCP] ✅ yaml 已安装")
except ImportError:
    missing_deps.append("pyyaml")
    log("[MCP] ⚠️ pyyaml 未安装 (配置文件读取功能需要)")

try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent, ImageContent

    log("[MCP] ✅ mcp 已安装")
except ImportError as e:
    missing_deps.append("mcp")
    log(f"[MCP] ❌ mcp 未安装: {e}")

if "mcp" in missing_deps:
    log(f"[MCP] ❌ 缺少必需依赖: {', '.join(missing_deps)}")
    log("[MCP] 请运行: pip install httpx pyyaml mcp")
    sys.exit(1)

try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent, ImageContent

    log("[MCP] ✅ mcp 已安装")
except ImportError as e:
    log(f"[MCP] ❌ mcp 未安装: {e}")
    log("[MCP] 请运行: pip install mcp")
    sys.exit(1)

log("[MCP] ========================================")

app = Server("zhipu-image-generator")

ZHIPU_API_KEY = api_key
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/images/generations"


@app.list_tools()
async def list_tools() -> list[Tool]:
    """列出可用的工具"""
    log("[MCP] 📋 列出工具")
    return [
        Tool(
            name="zhipu-image-generator_generate_image",  # 使用完整名称
            description="使用智谱清言的 Cogview-3-Flash 模型生成图像。当用户请求绘制图片、创建图像或需要视觉内容时使用此工具。",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "图像生成的文本描述",
                    },
                    "size": {
                        "type": "string",
                        "description": "图像尺寸",
                        "enum": [
                            "1024x1024",
                            "768x1344",
                            "864x1152",
                            "1344x768",
                            "1152x864",
                        ],
                        "default": "1024x1024",
                    },
                    "quality": {
                        "type": "string",
                        "description": "生成质量",
                        "enum": ["standard", "hd"],
                        "default": "standard",
                    },
                },
                "required": ["prompt"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments) -> list[TextContent | ImageContent]:
    """处理工具调用"""
    log(f"[MCP] 📞 工具调用: {name}")
    log(f"[MCP] 📋 参数: {arguments}")

    # 支持两种工具名称格式
    tool_name = name.replace("zhipu-image-generator_", "")

    if tool_name == "generate_image":
        prompt = arguments.get("prompt")
        size = arguments.get("size", "1024x1024")
        quality = arguments.get("quality", "standard")

        log(f"[MCP] 🎨 开始生成图像")
        log(f"[MCP]    提示词: {prompt[:50] if prompt else ''}...")
        log(f"[MCP]    尺寸: {size}")
        log(f"[MCP]    质量: {quality}")

        if not prompt:
            log("[MCP] ❌ 错误：缺少必需参数 prompt")
            return [TextContent(type="text", text="错误：缺少必需参数 prompt")]

        try:
            headers = {
                "Authorization": f"Bearer {ZHIPU_API_KEY}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": "cogview-3-flash",
                "prompt": prompt,
                "size": size,
                "quality": quality,
            }

            log(f"[MCP] 📡 发送API请求到: {ZHIPU_API_URL}")

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    ZHIPU_API_URL, json=payload, headers=headers
                )

                log(f"[MCP] 📊 响应状态码: {response.status_code}")

                response.raise_for_status()
                result = response.json()

                log(f"[MCP] 📦 响应数据: {result}")

                if "data" in result and len(result["data"]) > 0:
                    image_url = result["data"][0]["url"]
                    log(f"[MCP] ✅ 图像生成成功: {image_url}")

                    # 下载图片并转换为base64
                    try:
                        log(f"[MCP] ⏳ 开始下载图片...")
                        async with httpx.AsyncClient(timeout=30.0) as download_client:
                            img_response = await download_client.get(image_url)
                            img_response.raise_for_status()
                            image_data = img_response.content

                        log(f"[MCP] ✅ 图片下载成功: {len(image_data)} bytes")

                        # 转换为base64
                        image_base64 = base64.b64encode(image_data).decode("utf-8")
                        log(f"[MCP] ✅ 图片base64编码完成: {len(image_base64)} 字符")

                        # 返回ImageContent
                        return [
                            ImageContent(
                                type="image", data=image_base64, mimeType="image/png"
                            )
                        ]
                    except Exception as download_error:
                        log(
                            f"[MCP] ❌ 图片下载失败: {type(download_error).__name__}: {download_error}"
                        )
                        log(f"[MCP] 📚 下载错误堆栈:\n{traceback.format_exc()}")
                        # 降级：返回URL文本
                        return [
                            TextContent(
                                type="text",
                                text=f"图像生成成功！\n图片URL：{image_url}\n\n提示词：{prompt}",
                            )
                        ]
                else:
                    log(f"[MCP] ❌ 错误：未返回有效的图片数据")
                    return [TextContent(type="text", text="错误：未返回有效的图片数据")]

        except Exception as e:
            log(f"[MCP] ❌ 异常: {type(e).__name__}: {e}")
            log(f"[MCP] 📚 堆栈跟踪:\n{traceback.format_exc()}")
            return [TextContent(type="text", text=f"图像生成失败：{str(e)}")]

    else:
        log(f"[MCP] ⚠️  未知工具: {name}")
        return [TextContent(type="text", text=f"未知工具：{name}")]


async def main():
    """启动服务器"""
    log("[MCP] 🎮 初始化MCP服务器...")

    try:
        import mcp.server.stdio

        log("[MCP] ✅ stdio模块已导入")
    except ImportError as e:
        log(f"[MCP] ❌ stdio模块导入失败: {e}")
        sys.exit(1)

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        log("[MCP] ✅ stdio服务器已创建")
        log("[MCP] 🎮 开始运行MCP服务器...")
        log("[MCP] 等待客户端连接...")

        try:
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
        except Exception as e:
            log(f"[MCP] ❌ 运行错误: {type(e).__name__}: {e}")
            log(f"[MCP] 📚 堆栈跟踪:\n{traceback.format_exc()}")
            raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("[MCP] ⚠️  服务器被用户中断")
    except Exception as e:
        log(f"[MCP] ❌ 致命错误: {type(e).__name__}: {e}")
        log(f"[MCP] 📚 堆栈跟踪:\n{traceback.format_exc()}")
        sys.exit(1)
