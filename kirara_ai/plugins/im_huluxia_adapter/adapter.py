import asyncio
import random
import time
from typing import Optional
from datetime import datetime
from kirara_ai.im.adapter import IMAdapter, UserProfileAdapter
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.im.profile import UserProfile
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.dispatch import WorkflowDispatcher
from kirara_ai.ioc.inject import Inject
from kirara_ai.database.manager import DatabaseManager
from .models import HuluxiaConfig, HuluxiaAdapterState
from .api_client import HuluxiaApiClient

logger = get_logger("HuluxiaAdapter")


class HuluxiaAdapter(IMAdapter, UserProfileAdapter):
    """
    葫芦侠 IM 适配器
    实现 Kirara AI 的 IMAdapter 接口
    """

    dispatcher: WorkflowDispatcher

    @Inject()
    def __init__(self, config: HuluxiaConfig, db_manager: DatabaseManager):
        self.config = config
        self.adapter_name = config.name if hasattr(config, "name") else "huluxia"
        self._db_manager = db_manager

        # 运行时状态
        self.is_running: bool = False
        self.polling_task: Optional[asyncio.Task] = None

        # API 客户端
        self.api_client: Optional[HuluxiaApiClient] = None

        # 登录信息
        self.user_id: Optional[str] = None
        self._key: Optional[str] = None

        # 时间戳管理（内存缓存）
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

        # 帖子信息（在 content 字段中）
        post = content.get("post", {})
        post_id = post.get("postID")

        logger.info(
            f"[CONVERT_MESSAGE] 解析帖子信息: post_id={post_id}, comment_id={comment_id}, post={post}"
        )

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

        logger.info(
            f"[SEND_MESSAGE] 开始发送消息: recipient={recipient.user_id}, display_name={recipient.display_name}"
        )

        # 1. 提取帖子ID和评论ID
        post_id = recipient.raw_metadata.get("post_id")
        comment_id = recipient.raw_metadata.get("comment_id", 0)

        logger.info(
            f"[SEND_MESSAGE] 帖子ID={post_id}, 评论ID={comment_id}, metadata={recipient.raw_metadata}"
        )

        if not post_id:
            logger.error("[SEND_MESSAGE] 无法获取帖子ID，无法发送消息")
            return

        # 2. 遍历消息元素，处理文本内容
        for element in message.message_elements:
            if isinstance(element, TextMessage):
                text = element.text

                logger.info(f"[SEND_MESSAGE] 原始消息内容: {text}")

                # 3. 敏感词过滤
                if self.config.sensitive_words:
                    text = self._replace_sensitive_words(text)
                    if text != element.text:
                        logger.debug(f"敏感词已过滤: '{element.text}' -> '{text}'")

                # 4. 检查字数，少于5个字则填充"."
                original_length = len(text)
                if original_length < 5:
                    fill_count = 5 - original_length
                    text = text + ("." * fill_count)
                    logger.info(
                        f"[SEND_MESSAGE] 字数不足5个，已填充 {fill_count} 个 '.': '{text}'"
                    )

                # 5. 等待发送延迟（3-5秒）
                await self._wait_for_comment_delay()

                # 6. 发送评论（最多重试3次）
                max_retries = 3
                original_text = text

                for retry_count in range(max_retries):
                    try:
                        logger.info(
                            f"[SEND_MESSAGE] 准备发送评论: retry={retry_count + 1}/{max_retries}, text={text}, post_id={post_id}, comment_id={comment_id}"
                        )

                        # 调用 API 创建评论
                        response = await self.api_client.create_comment(
                            _key=self._key,
                            market_id=self.config.market_id,
                            post_id=post_id,
                            comment_id=comment_id,
                            text=text,
                        )

                        logger.info(
                            f"[SEND_MESSAGE] API响应: code={response.get('code')}, msg={response.get('msg')}, status={response.get('status')}, 完整响应={response}"
                        )

                        # 6. 检查响应状态
                        response_text = response.get("msg", "")

                        # 6.1 处理审核状态
                        if "需要审核" in response_text or response.get("code") == 201:
                            logger.info(f"[SEND_MESSAGE] 评论进入审核: {response_text}")

                            # 等待3-4秒避免触发速度限制
                            delay_seconds = random.uniform(3.0, 4.0)
                            logger.info(
                                f"[SEND_MESSAGE] 等待 {delay_seconds:.2f}秒 后发送审核通知..."
                            )
                            await asyncio.sleep(delay_seconds)

                            # 生成幸运数字并发送审核通知
                            lucky_num = self._generate_lucky_number()
                            audit_text = f"评论审核中，请耐心等待~\n您的幸运数字是{lucky_num}[彩虹]"

                            logger.info(f"[SEND_MESSAGE] 发送审核通知: {audit_text}")

                            try:
                                await self.api_client.create_comment(
                                    _key=self._key,
                                    market_id=self.config.market_id,
                                    post_id=post_id,
                                    comment_id=0,  # 审核通知作为新评论
                                    text=audit_text,
                                )
                                logger.info("[SEND_MESSAGE] 审核通知已发送")
                            except Exception as audit_error:
                                logger.warning(
                                    f"[SEND_MESSAGE] 审核通知发送失败(不影响审核): {audit_error}"
                                )

                            break  # 审核处理完成，退出重试循环

                        # 6.2 处理重复评论
                        elif "请勿提交重复评论" in response_text:
                            logger.warning(
                                f"[SEND_MESSAGE] 检测到重复评论: {response_text}"
                            )
                            if retry_count < max_retries - 1:
                                # 添加幸运数字后重试
                                lucky_num = self._generate_lucky_number()
                                text = f"{original_text}\n您的幸运数字是{lucky_num}"
                                logger.warning(
                                    f"[SEND_MESSAGE] 添加幸运数字后重试: {retry_count + 1}/{max_retries}, 新文本={text}"
                                )
                                await asyncio.sleep(0.5)  # 额外等待 500ms
                                continue
                            else:
                                logger.error(
                                    "[SEND_MESSAGE] 重复评论重试次数已用尽，放弃发送"
                                )
                                raise Exception("评论发送失败：重复评论")

                        # 6.3 其他成功情况
                        else:
                            logger.info(f"[SEND_MESSAGE] 评论发送成功: {text[:50]}...")
                            break  # 发送成功，退出重试循环

                    except Exception as e:
                        logger.error(
                            f"[SEND_MESSAGE] 发送异常 (retry {retry_count + 1}/{max_retries}): {e}"
                        )
                        if retry_count < max_retries - 1:
                            logger.warning(
                                f"[SEND_MESSAGE] 评论发送失败，重试 {retry_count + 1}/{max_retries}: {e}"
                            )
                            await asyncio.sleep(1)  # 等待 1 秒后重试
                        else:
                            logger.error(
                                f"[SEND_MESSAGE] 评论发送失败，重试次数已用尽: {e}"
                            )
                            raise

                # 7. 更新最后发送时间
                self._last_send_time = time.time()
                logger.info(f"[SEND_MESSAGE] 消息发送流程完成，已更新最后发送时间")

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

        # 3. 从数据库加载最后处理时间
        logger.info(f"[START] 准备从数据库加载最后处理时间...")
        self._load_last_time_from_db()
        logger.info(
            f"[START] 加载完成，当前fallback_last_time={self._fallback_last_time}"
        )

        # 4. 启动轮询任务
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
        return random.randint(1, 50)

    async def _wait_for_comment_delay(self):
        """等待评论发送延迟（3-5秒随机）"""
        if self._last_send_time > 0:
            current_time = time.time()
            elapsed_ms = int((current_time - self._last_send_time) * 1000)

            # 3-5秒随机延迟
            required_delay_ms = random.randint(3000, 5000)
            if elapsed_ms < required_delay_ms:
                wait_ms = required_delay_ms - elapsed_ms
                logger.debug(f"等待 {wait_ms}ms ({wait_ms / 1000:.2f}秒) 后发送评论")
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
                max_processed_time = last_time

                for msg_item in new_messages:
                    try:
                        content = msg_item.get("content", {})
                        create_time = content.get("createTime")
                        text = content.get("text", "")

                        logger.info(
                            f"[POLLING] 处理消息: create_time={create_time}, user={content.get('user', {}).get('nick')}, text={text[:30]}..."
                        )

                        # 转换消息
                        im_message = await self.convert_to_message(msg_item)

                        # 分发消息
                        logger.info(f"[POLLING] 开始分发消息到工作流...")
                        await self.dispatcher.dispatch(self, im_message)
                        logger.info(f"[POLLING] 消息分发完成")

                        # 记录已处理的最大时间戳
                        if create_time > max_processed_time:
                            max_processed_time = create_time

                        logger.info(
                            f"[POLLING] 消息处理成功: create_time={create_time}, 内容={im_message.content[:30]}..."
                        )

                    except Exception as e:
                        logger.error(f"[POLLING] 处理单条消息失败: {e}")
                        import traceback

                        logger.error(traceback.format_exc())
                        # 继续处理下一条

                # 6. 所有消息处理完成后，统一更新时间戳
                if max_processed_time > last_time:
                    self._save_last_processed_time(max_processed_time)
                    # 保存到数据库
                    self._save_last_time_to_db(max_processed_time)
                    logger.info(
                        f"[POLLING] 批量处理完成，更新最后处理时间: {last_time} -> {max_processed_time}, 将保存到数据库"
                    )

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
        """加载最后处理时间（使用内存缓存）"""
        return self._fallback_last_time

    def _save_last_processed_time(self, timestamp_ms: int):
        """保存最后处理时间（更新内存缓存）"""
        self._fallback_last_time = max(self._fallback_last_time, timestamp_ms)

    def _load_last_time_from_db(self):
        """从数据库加载最后处理时间"""
        session = None
        try:
            session = self._db_manager.get_session()
            state = (
                session.query(HuluxiaAdapterState)
                .filter_by(adapter_name=self.adapter_name)
                .first()
            )

            if state and state.last_message_time:
                # 将 DateTime 对象转换为毫秒时间戳
                timestamp_ms = int(state.last_message_time.timestamp() * 1000)
                self._fallback_last_time = timestamp_ms
                logger.info(
                    f"[DB_LOAD] 从数据库加载最后处理时间: DateTime={state.last_message_time}, timestamp_ms={timestamp_ms}, adapter_name={self.adapter_name}"
                )
            else:
                logger.info(
                    f"[DB_LOAD] 数据库中没有记录，使用默认时间戳: {self._fallback_last_time}"
                )
                logger.debug(
                    f"[DB_LOAD] state存在: {state is not None}, last_message_time存在: {state.last_message_time if state else None}"
                )

            session.commit()
        except Exception as e:
            logger.error(f"[DB_LOAD] 从数据库加载时间戳失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            if session:
                session.rollback()
        finally:
            if session:
                session.close()

    def _save_last_time_to_db(self, timestamp_ms: int):
        """保存最后处理时间到数据库"""
        session = None
        try:
            session = self._db_manager.get_session()

            # 查找或创建状态记录
            state = (
                session.query(HuluxiaAdapterState)
                .filter_by(adapter_name=self.adapter_name)
                .first()
            )
            if not state:
                state = HuluxiaAdapterState(adapter_name=self.adapter_name)
                session.add(state)

            # 更新最后消息时间
            state.last_message_time = datetime.fromtimestamp(timestamp_ms / 1000)
            state.updated_at = datetime.now()

            session.commit()

            logger.info(
                f"[DB_SAVE] 时间戳已保存到数据库: timestamp_ms={timestamp_ms}, DateTime={datetime.fromtimestamp(timestamp_ms / 1000)}, adapter_name={self.adapter_name}"
            )
        except Exception as e:
            logger.error(f"[DB_SAVE] 保存时间戳到数据库失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            if session:
                session.rollback()
        finally:
            if session:
                session.close()
