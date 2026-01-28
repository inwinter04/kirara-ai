import asyncio
from typing import Optional
from kirara_ai.im.adapter import IMAdapter, UserProfileAdapter
from kirara_ai.im.message import IMMessage, TextMessage
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

    dispatcher: WorkflowDispatcher

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

        # 时间戳管理
        self._fallback_last_time: int = 0

        # 发送延迟控制
        self._last_send_time: float = 0  # 上次发送时间戳（秒）

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
        content = raw_message.get("content", {})

        # 用户信息
        user = content.get("user", {})
        user_id = str(user.get("userID"))
        nick = user.get("nick", "")

        # 评论信息
        comment_id = content.get("commentID")

        # 帖子信息（原始消息包含 post 字段）
        post = raw_message.get("post", {})
        post_id = post.get("postID")

        # 创建发送者，存储 post_id 和 comment_id
        sender = ChatSender.from_c2c_chat(
            user_id=user_id,
            display_name=nick,
            metadata={
                "comment_id": comment_id,
                "post_id": post_id,  # 新增：存储帖子ID用于回复
            },
        )

        # 消息内容
        text = content.get("text", "")
        message_elements = [TextMessage(text=text)]

        return IMMessage(
            sender=sender,
            message_elements=message_elements,
            raw_message=raw_message,
        )

    async def send_message(self, message: IMMessage, recipient: ChatSender):
        """
        发送消息到葫芦侠

        Args:
            message: 要发送的 IMMessage
            recipient: 接收者（ChatSender对象）
        """
        import time

        # 1. 提取帖子ID和评论ID
        post_id = recipient.raw_metadata.get("post_id")
        comment_id = recipient.raw_metadata.get("comment_id", 0)

        if not post_id:
            logger.error("无法获取帖子ID，无法发送消息")
            return

        # 2. 遍历消息元素，处理文本内容
        for element in message.message_elements:
            if isinstance(element, TextMessage):
                text = element.text

                # 3. 敏感词过滤
                if self.config.sensitive_words:
                    text = self._replace_sensitive_words(text)
                    if text != element.text:
                        logger.debug(f"敏感词已过滤: '{element.text}' -> '{text}'")

                # 4. 等待发送延迟
                await self._wait_for_comment_delay()

                # 5. 发送评论（最多重试3次）
                max_retries = 3
                original_text = text

                for retry_count in range(max_retries):
                    try:
                        # 调用 API 创建评论
                        response = await self.api_client.create_comment(
                            _key=self._key,
                            market_id=self.config.market_id,
                            post_id=post_id,
                            comment_id=comment_id,
                            text=text,
                        )

                        # 6. 检查响应状态
                        response_text = response.get("msg", "")

                        # 6.1 处理审核状态
                        if "需要审核" in response_text or response.get("code") == 201:
                            logger.info("评论进入审核，发送审核通知")

                            # 生成幸运数字并发送审核通知
                            lucky_num = self._generate_lucky_number()
                            audit_text = f"评论审核中，请耐心等待~\n您的幸运数字是{lucky_num}[彩虹]"

                            await self.api_client.create_comment(
                                _key=self._key,
                                market_id=self.config.market_id,
                                post_id=post_id,
                                comment_id=0,  # 审核通知作为新评论
                                text=audit_text,
                            )

                            break  # 审核处理完成，退出重试循环

                        # 6.2 处理重复评论
                        elif "请勿提交重复评论" in response_text:
                            if retry_count < max_retries - 1:
                                # 添加幸运数字后重试
                                lucky_num = self._generate_lucky_number()
                                text = f"{original_text}\n您的幸运数字是{lucky_num}"
                                logger.warning(
                                    f"检测到重复评论，添加幸运数字后重试: {retry_count + 1}/{max_retries}"
                                )
                                await asyncio.sleep(0.5)  # 额外等待 500ms
                                continue
                            else:
                                logger.error("重复评论重试次数已用尽，放弃发送")
                                raise Exception("评论发送失败：重复评论")

                        # 6.3 其他成功情况
                        else:
                            logger.info(f"评论发送成功: {text[:50]}...")
                            break  # 发送成功，退出重试循环

                    except Exception as e:
                        if retry_count < max_retries - 1:
                            logger.warning(
                                f"评论发送失败，重试 {retry_count + 1}/{max_retries}: {e}"
                            )
                            await asyncio.sleep(1)  # 等待 1 秒后重试
                        else:
                            logger.error(f"评论发送失败，重试次数已用尽: {e}")
                            raise

                # 7. 更新最后发送时间
                self._last_send_time = time.time()

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

    def _replace_sensitive_words(self, text: str) -> str:
        """替换敏感词，将敏感词替换为空格"""
        filtered_text = text
        for word in self.config.sensitive_words:
            filtered_text = filtered_text.replace(word, " ")
        return filtered_text

    @staticmethod
    def _generate_lucky_number() -> int:
        """生成幸运数字（1-50）"""
        import random

        return random.randint(1, 50)

    async def _wait_for_comment_delay(self):
        """等待评论发送延迟"""
        if self._last_send_time > 0:
            import time

            current_time = time.time()
            elapsed_ms = int((current_time - self._last_send_time) * 1000)

            if elapsed_ms < self.config.comment_delay_ms:
                wait_ms = self.config.comment_delay_ms - elapsed_ms
                logger.debug(f"等待 {wait_ms}ms 后发送评论")
                await asyncio.sleep(wait_ms / 1000)

    async def _polling_loop(self):
        """轮询新消息的后台任务"""
        logger.info(f"开始轮询: {self.adapter_name}")

        while self.is_running:
            try:
                # 1. 获取最后处理时间（带数据库回退）
                last_time = self._load_last_processed_time()

                # 2. 调用API获取消息列表
                response = await self.api_client.get_message_list(
                    _key=self._key, market_id=self.config.market_id, start=0, count=20
                )

                # 3. 检查响应状态
                if response.get("status") != 1:
                    logger.warning(
                        f"获取消息失败: status={response.get('status')}, "
                        f"msg={response.get('msg')}, "
                        f"response={response}"
                    )
                    await asyncio.sleep(self.config.poll_interval)
                    continue

                # 4. 过滤并排序新消息
                messages = response.get("datas", [])
                new_messages = [
                    msg
                    for msg in messages
                    if msg.get("content", {}).get("createTime", 0) > last_time
                ]

                # 按时间戳升序排序
                new_messages.sort(
                    key=lambda x: x.get("content", {}).get("createTime", 0)
                )

                # 5. 处理每条新消息
                for msg_item in new_messages:
                    try:
                        content = msg_item.get("content", {})
                        create_time = content.get("createTime")

                        # 转换消息
                        im_message = await self.convert_to_message(msg_item)

                        # 分发消息
                        await self.dispatcher.dispatch(self, im_message)

                        # 更新最后处理时间
                        self._save_last_processed_time(create_time)

                        logger.debug(
                            f"处理消息: 用户={im_message.sender.display_name}, "
                            f"内容={im_message.content[:50]}..."
                        )

                    except Exception as e:
                        logger.error(f"处理单条消息失败: {e}")
                        # 继续处理下一条

                # 6. 等待下一次轮询
                await asyncio.sleep(self.config.poll_interval)

            except asyncio.CancelledError:
                logger.info(f"轮询任务被取消: {self.adapter_name}")
                break
            except Exception as e:
                logger.error(f"轮询异常: {e}")
                if self.is_running:
                    await asyncio.sleep(self.config.poll_interval)

    def _load_last_processed_time(self) -> int:
        """加载最后处理时间（使用内存存储）"""
        return self._fallback_last_time

    def _save_last_processed_time(self, timestamp_ms: int):
        """保存最后处理时间（使用内存存储）"""
        self._fallback_last_time = max(self._fallback_last_time, timestamp_ms)
