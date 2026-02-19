import os
from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.plugin import Plugin
from kirara_ai.web.app import WebServer
from .adapter import HuluxiaAdapter
from .blocks import GetHuluxiaCredentials
from .models import HuluxiaConfig

logger = get_logger("HuluxiaAdapterPlugin")


class HuluxiaAdapterPlugin(Plugin):
    """
    葫芦侠适配器插件
    继承 Kirara AI 的 Plugin 基类
    """

    web_server: WebServer

    def __init__(self):
        pass

    def on_load(self):
        # 注册适配器到 Kirara AI
        self.im_registry.register(
            "huluxia",
            HuluxiaAdapter,
            HuluxiaConfig,
            "葫芦侠",
            "葫芦侠社区平台",
            "由神奇小冬天开发的葫芦侠适配器。[冬天的博客](https://www.iamdt.cn)",
        )

        # 注册工作流Block
        self.block_registry.register(
            "get_huluxia_credentials",
            "huluxia",
            GetHuluxiaCredentials,
            "葫芦侠: 获取登录凭证",
        )
        logger.info("已注册葫芦侠工作流Block")

        # 注册静态资源（平台图标）
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "huluxia.png")
        if os.path.exists(icon_path):
            self.web_server.add_static_assets("/assets/icons/im/huluxia.png", icon_path)
            logger.info("已注册平台图标")
        else:
            logger.warning(f"平台图标文件不存在: {icon_path}")

    def on_start(self):
        """插件启动时的初始化"""
        logger.info("启动葫芦侠适配器插件")

    def on_stop(self):
        """插件停止时的清理"""
        logger.info("停止葫芦侠适配器插件")
