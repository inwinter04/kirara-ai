import asyncio
from typing import Optional
from kirara_ai.im.adapter import IMAdapter, UserProfileAdapter
from kirara_ai.im.message import IMMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.im.profile import UserProfile
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.dispatch import WorkflowDispatcher
from .models import HuluxiaConfig
from .api_client import HuluxiaApiClient

logger = get_logger("HuluxiaAdapter")


class HuluxiaAdapter(IMAdapter, UserProfileAdapter):
    """
    葫芦侠 IM 适配器
    实现 Kirara AI 的 IMAdapter 接口
    """

    def __init__(self, config: HuluxiaConfig):
        self.config = config
        self.adapter_name = config.name if hasattr(config, "name") else "huluxia"

        # 运行时状态
        self.is_running: bool = False
        self.polling_task: Optional[asyncio.Task] = None

        # API 客户端
        self.api_client: Optional[HuluxiaApiClient] = None

        # 登录信息
        self.user_id: Optional[str] = None
        self._key: Optional[str] = None

        logger.info(f"初始化葫芦侠适配器: {self.adapter_name}")

    # ===== IMAdapter 接口实现 =====

    async def convert_to_message(self, raw_message: dict) -> IMMessage:
        """
        将葫芦侠原生消息转换为 Kirara IMMessage

        Args:
            raw_message: 葫芦侠 API 返回的原始消息

        Returns:
            IMMessage 对象
        """
        # TODO: 根据实际消息API实现消息转换
        logger.warning("convert_to_message 尚未实现，需要提供消息API文档")
        raise NotImplementedError("消息转换功能尚未实现，需要提供消息API文档")

    async def send_message(self, message: IMMessage, recipient: ChatSender):
        """
        通过 Kirara IMMessage 发送消息到葫芦侠

        Args:
            message: 要发送的 IMMessage
            recipient: 接收者
        """
        # TODO: 根据实际消息API实现消息发送
        logger.warning("send_message 尚未实现，需要提供消息API文档")
        raise NotImplementedError("消息发送功能尚未实现，需要提供消息API文档")

    async def start(self):
        """启动适配器"""
        logger.info(f"启动葫芦侠适配器: {self.adapter_name}")

        # 1. 创建并打开 API 客户端
        self.api_client = HuluxiaApiClient(
            base_url=self.config.base_url,
            device_code=self.config.device_code,
            timeout=self.config.timeout,
        )
        await self.api_client.open()

        # 2. 执行登录
        try:
            login_result = await self.api_client.login(
                account=self.config.account,
                password=self.config.password,
                login_main_site=(self.config.market_id == "tool_web"),
            )

            self.user_id = login_result["user_id"]
            self._key = login_result["key"]

            logger.info(f"登录成功: 用户ID={self.user_id}")

        except Exception as e:
            logger.error(f"登录失败: {e}")
            await self.api_client.close()
            self.api_client = None
            raise

        # 3. 启动轮询任务
        self.is_running = True
        self.polling_task = asyncio.create_task(self._polling_loop())
        logger.info(f"轮询任务已启动: {self.adapter_name}")

    async def stop(self):
        """停止适配器"""
        logger.info(f"停止葫芦侠适配器: {self.adapter_name}")

        self.is_running = False

        # 停止轮询任务
        if self.polling_task:
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
            self.polling_task = None

        # 关闭 API 客户端
        if self.api_client:
            await self.api_client.close()

        logger.info(f"适配器已停止: {self.adapter_name}")

    # ===== UserProfileAdapter 接口实现 =====

    async def query_user_profile(self, chat_sender: ChatSender) -> UserProfile:
        """
        查询用户资料

        Args:
            chat_sender: 聊天发送者

        Returns:
            UserProfile 对象
        """
        # TODO: 根据实际API实现用户资料查询
        logger.warning("query_user_profile 尚未实现，需要提供用户API文档")
        return UserProfile(
            user_id=chat_sender.user_id, display_name=chat_sender.display_name
        )

    # ===== 私有方法 =====

    async def _polling_loop(self):
        """轮询新消息的后台任务"""
        logger.info(f"开始轮询: {self.adapter_name}")

        while self.is_running:
            try:
                # TODO: 根据消息API实现轮询逻辑
                logger.warning("_polling_loop 尚未实现，需要提供消息API文档")

                # 等待下一次轮询
                await asyncio.sleep(self.config.poll_interval)

            except Exception as e:
                logger.error(f"轮询失败: {e}")
                if self.is_running:
                    await asyncio.sleep(self.config.poll_interval)
