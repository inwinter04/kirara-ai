import asyncio
import random
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from kirara_ai.im.adapter import IMAdapter, UserProfileAdapter
from kirara_ai.im.message import IMMessage, TextMessage, ImageMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.im.profile import UserProfile
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.dispatch import WorkflowDispatcher
from kirara_ai.ioc.inject import Inject
from kirara_ai.database.manager import DatabaseManager
from .models import HuluxiaConfig, HuluxiaAdapterState
from .api_client import HuluxiaApiClient
from .heat.scheduler import HeatTaskScheduler
from .heat.executor import HeatTaskExecutor

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
        self.key_check_task: Optional[asyncio.Task] = None

        # API 客户端
        self.api_client: Optional[HuluxiaApiClient] = None

        # 登录信息
        self.user_id: Optional[str] = None
        self._key: Optional[str] = None

        # 时间戳管理（内存缓存）
        self._fallback_last_time: int = 0

        # 发送延迟控制
        self._last_send_time: float = 0  # 上次发送时间戳（秒）

        # 热度任务调度器
        self._heat_scheduler: Optional[HeatTaskScheduler] = None

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

        # 2. 收集所有文本消息内容
        text_messages = [
            elem for elem in message.message_elements if isinstance(elem, TextMessage)
        ]

        # 2.5. 收集图片消息
        image_messages = [
            elem for elem in message.message_elements if isinstance(elem, ImageMessage)
        ]

        # 2.6. 验证约束条件
        if len(image_messages) > 9:
            logger.error(
                f"[SEND_MESSAGE] 图片数量超限（{len(image_messages)} > 9），取消发送"
            )
            return

        # 2.7. 如果有图片但没有文本，使用默认文本
        default_texts = [
            "我画好啦[吐舌]",
            "给你画了一个[酷]",
            "画好了呀[星星月亮]",
            "这是我的作品[疑问]",
            "看看怎么样呀[吐舌]",
        ]
        if image_messages and not text_messages:
            default_text = random.choice(default_texts)
            text_messages = [default_text]
            logger.info(
                f"[SEND_MESSAGE] 有图片但没有文本，使用默认文本: {default_text}"
            )

        # 3. 处理消息数量，确保最多2条
        if len(text_messages) >= 3:
            # 在中间位置分割消息，使字数更加均衡
            # 计算总字数，找到最接近中间的分割点
            total_texts = [msg.text for msg in text_messages]
            total_length = sum(len(text) for text in total_texts)

            # 从头开始累加，找到最接近总长度一半的位置
            current_length = 0
            split_index = 0
            for i, text in enumerate(total_texts):
                if abs(current_length + len(text) - total_length / 2) < abs(
                    current_length - total_length / 2
                ):
                    current_length += len(text)
                    split_index = i + 1
                else:
                    break

            # 分割成两组，确保split_index至少为1
            split_index = max(1, split_index)
            first_part = "".join(total_texts[:split_index])
            second_part = "".join(total_texts[split_index:])
            text_messages = [first_part, second_part]
            logger.info(
                f"[SEND_MESSAGE] 消息过多，在中间分割：第1条='{first_part[:50]}...'({len(first_part)}字), 第2条='{second_part[:50]}...'({len(second_part)}字)"
            )
        elif len(text_messages) == 2:
            # 有2条消息，保持原样
            text_messages = [text_messages[0].text, text_messages[1].text]
        elif len(text_messages) == 1:
            # 只有1条消息
            text_messages = [text_messages[0].text]
        else:
            logger.warning("[SEND_MESSAGE] 没有文本消息")
            return

        # 3.5. 上传图片（如果有）
        image_fids = []
        if image_messages:
            logger.info(f"[SEND_MESSAGE] 开始上传 {len(image_messages)} 张图片")
            for idx, img_msg in enumerate(image_messages):
                try:
                    logger.info(
                        f"[SEND_MESSAGE] 上传第 {idx + 1}/{len(image_messages)} 张图片"
                    )
                    img_data = await img_msg.get_data()
                    filename = img_msg.path if img_msg.path else f"image_{idx}.jpg"
                    fid = await self.api_client.upload_image(
                        _key=self._key, image_data=img_data, filename=filename
                    )
                    image_fids.append(fid)
                    logger.info(
                        f"[SEND_MESSAGE] 第 {idx + 1} 张图片上传成功: fid={fid}"
                    )
                except Exception as e:
                    logger.warning(
                        f"[SEND_MESSAGE] 第 {idx + 1} 张图片上传失败: {e}，跳过该图片"
                    )
                    # 继续上传下一张图片

            if not image_fids:
                logger.warning("[SEND_MESSAGE] 所有图片上传失败，将发送纯文本评论")
            else:
                logger.info(
                    f"[SEND_MESSAGE] 成功上传 {len(image_fids)}/{len(image_messages)} 张图片"
                )

        # 4. 根据消息数量决定如何处理
        if len(text_messages) == 1:
            # 只有1条文本，可以分割成最多2条
            text = text_messages[0]

            # 5. 敏感词过滤
            if self.config.sensitive_words:
                original_text_for_filter = text
                text = self._replace_sensitive_words(text)
                if text != original_text_for_filter:
                    logger.debug(
                        f"敏感词已过滤: '{original_text_for_filter}' -> '{text}'"
                    )

            # 6. 处理消息分割：检测\n\n数量
            double_newline_count = text.count("\n\n")

            if double_newline_count >= 2:
                # 有2个及以上\n\n，需要分割成两条消息发送
                # 先将所有\n\n替换为\n
                normalized_text = text.replace("\n\n", "\n")

                # 找到所有\n的位置
                newline_positions = [
                    i for i, char in enumerate(normalized_text) if char == "\n"
                ]

                # 在中间位置分割，确保两部分都不为空
                split_pos = newline_positions[len(newline_positions) // 2]
                text_part1 = normalized_text[:split_pos].strip()
                text_part2 = normalized_text[split_pos + 1 :].strip()

                # 如果分割后有空字符串，尝试其他分割点
                if not text_part1 or not text_part2:
                    logger.warning(
                        f"[SEND_MESSAGE] 分割后有空字符串（第1部分={len(text_part1)}字, 第2部分={len(text_part2)}字），尝试其他分割点"
                    )
                    # 尝试所有分割点，找到一个两部分都不为空的
                    found_valid_split = False
                    for pos in newline_positions:
                        part1 = normalized_text[:pos].strip()
                        part2 = normalized_text[pos + 1 :].strip()
                        if part1 and part2:
                            split_pos = pos
                            text_part1 = part1
                            text_part2 = part2
                            found_valid_split = True
                            break

                    if not found_valid_split:
                        # 所有分割点都会产生空字符串，使用第一个分割点
                        split_pos = newline_positions[0]
                        text_part1 = normalized_text[:split_pos].strip()
                        text_part2 = normalized_text[split_pos + 1 :].strip()
                        logger.warning(
                            f"[SEND_MESSAGE] 所有分割点都会产生空字符串，使用第一个分割点：第1部分={len(text_part1)}字, 第2部分={len(text_part2)}字"
                        )

                try:
                    # 发送第一条消息（带图片）
                    images_param = ",".join(image_fids) if image_fids else ""
                    await self._send_single_comment(
                        text_part1, post_id, comment_id, images_param
                    )
                except Exception as e:
                    logger.error(f"[SEND_MESSAGE] 第1部分发送失败: {e}")
                    raise

                try:
                    # 等待回复间隔
                    await self._wait_for_comment_delay()

                    # 发送第二条消息（不带图片）
                    await self._send_single_comment(text_part2, post_id, comment_id, "")
                except Exception as e:
                    logger.error(
                        f"[SEND_MESSAGE] 第2部分发送失败（第1部分已发送）: {e}"
                    )
                    raise
            else:
                # 正常处理：替换\n\n为\n
                text = text.replace("\n\n", "\n")

                # 7. 发送单条评论（带图片）
                images_param = ",".join(image_fids) if image_fids else ""
                await self._send_single_comment(text, post_id, comment_id, images_param)

        elif len(text_messages) == 2:
            # 有2条文本，直接发送，不再分割
            for idx, text in enumerate(text_messages):
                logger.debug(
                    f"[SEND_MESSAGE] 处理第 {idx + 1}/{len(text_messages)} 条消息"
                )

                # 5. 敏感词过滤
                if self.config.sensitive_words:
                    original_text_for_filter = text
                    text = self._replace_sensitive_words(text)
                    if text != original_text_for_filter:
                        logger.debug(
                            f"敏感词已过滤: '{original_text_for_filter}' -> '{text}'"
                        )

                # 6. 替换\n\n为\n，不分割
                text = text.replace("\n\n", "\n")

                # 如果不是第一条，等待回复间隔
                if idx > 0:
                    await self._wait_for_comment_delay()

                # 7. 发送单条评论（仅在第一条带图片）
                images_param = ",".join(image_fids) if image_fids and idx == 0 else ""
                await self._send_single_comment(text, post_id, comment_id, images_param)

        # 9. 更新最后发送时间
        self._last_send_time = time.time()

    async def _send_single_comment(
        self, text: str, post_id: str, comment_id: int, images: str = ""
    ):
        """
        发送单条评论（内部方法，不包含延迟逻辑，延迟由调用方控制）

        Args:
            text: 评论内容
            post_id: 帖子ID
            comment_id: 评论ID
            images: 图片 fid 列表（逗号分隔），仅在第一条消息时传递
        """
        # 检查字数，少于5个字则填充"."
        original_length = len(text)
        if original_length < 5:
            fill_count = 5 - original_length
            text = text + ("." * fill_count)
            logger.debug(
                f"[SEND_SINGLE_COMMENT] 字数不足5个，已填充 {fill_count} 个 '.'"
            )

        # 发送评论（最多重试3次）
        max_retries = 3
        original_text = text

        for retry_count in range(max_retries):
            try:
                logger.info(
                    f"[SEND_SINGLE_COMMENT] 准备发送评论: retry={retry_count + 1}/{max_retries}, text={text}, post_id={post_id}, comment_id={comment_id}, images={images}"
                )

                # 调用 API 创建评论
                response = await self.api_client.create_comment(
                    _key=self._key,
                    market_id=self.config.market_id,
                    post_id=post_id,
                    comment_id=comment_id,
                    text=text,
                    images=images,
                )

                # 检查响应状态
                response_text = response.get("msg", "")

                # 处理未登录状态
                if "未登录" in response_text:
                    logger.warning(
                        f"[SEND_SINGLE_COMMENT] 检测到未登录，尝试重新登录..."
                    )
                    relogin_success = await self._relogin()
                    if relogin_success:
                        logger.info(
                            "[SEND_SINGLE_COMMENT] 重新登录成功，使用新的key重试发送..."
                        )
                        await asyncio.sleep(1)
                        continue
                    else:
                        logger.error("[SEND_SINGLE_COMMENT] 重新登录失败，放弃发送")
                        raise Exception("评论发送失败：重新登录失败")

                # 处理重复评论
                elif "请勿提交重复评论" in response_text:
                    logger.warning(
                        f"[SEND_SINGLE_COMMENT] 检测到重复评论: {response_text}"
                    )
                    if retry_count < max_retries - 1:
                        lucky_num = self._generate_lucky_number()
                        text = f"{original_text}\n您的幸运数字是{lucky_num}"
                        logger.warning(
                            f"[SEND_SINGLE_COMMENT] 添加幸运数字后重试: {retry_count + 1}/{max_retries}, 新文本={text}"
                        )
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        logger.error(
                            "[SEND_SINGLE_COMMENT] 重复评论重试次数已用尽，放弃发送"
                        )
                        raise Exception("评论发送失败：重复评论")
                else:
                    # 评论发送成功，打印响应信息
                    response_code = response.get("code")
                    response_status = response.get("status")
                    comment_id = response.get("commentID")
                    logger.info(
                        f"[SEND_SINGLE_COMMENT] 评论响应: code={response_code}, status={response_status}, comment_id={comment_id}, msg={response_text}"
                    )
                    break

            except Exception as e:
                logger.error(
                    f"[SEND_SINGLE_COMMENT] 发送异常 (retry {retry_count + 1}/{max_retries}): {e}"
                )
                if retry_count < max_retries - 1:
                    logger.warning(
                        f"[SEND_SINGLE_COMMENT] 评论发送失败，重试 {retry_count + 1}/{max_retries}: {e}"
                    )
                    await asyncio.sleep(1)
                else:
                    logger.error(
                        f"[SEND_SINGLE_COMMENT] 评论发送失败，重试次数已用尽: {e}"
                    )
                    raise

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

        # 2. 尝试从数据库加载登录信息
        login_info_loaded = self._load_login_info_from_db()
        need_login = True

        if login_info_loaded:
            now = datetime.now()
            expires_at = login_info_loaded["key_expires_at"]
            logger.info(
                f"[START] 从数据库加载的登录信息: user_id={self.user_id}, key_expires_at={expires_at}"
            )

            # 检查是否过期
            if now < expires_at:
                # 未过期，验证key是否有效
                logger.info("[START] Key未过期，验证key有效性...")
                key_valid = await self.api_client.verify_login_status()
                if key_valid:
                    logger.info("[START] Key有效，使用原有凭证")
                    need_login = False
                else:
                    logger.warning("[START] Key无效，需要重新登录")
            else:
                logger.warning(f"[START] Key已过期: {expires_at}，需要重新登录")
        else:
            logger.info("[START] 数据库中没有登录信息，需要登录")

        # 3. 如果需要登录，执行登录
        if need_login:
            try:
                login_result = await self.api_client.login(
                    account=self.config.account,
                    password=self.config.password,
                    login_main_site=(self.config.market_id == "tool_web"),
                )

                self.user_id = login_result["user_id"]
                self._key = login_result["key"]

                # 保存登录信息到数据库
                key_expires_at = datetime.now() + timedelta(days=10)
                self._save_login_info_to_db(self.user_id, self._key, key_expires_at)

                logger.info(
                    f"登录成功: 用户ID={self.user_id}, key过期时间={key_expires_at}"
                )

            except Exception as e:
                logger.error(f"登录失败: {e}")
                await self.api_client.close()
                self.api_client = None
                raise

        # 4. 从数据库加载最后处理时间
        logger.info(f"[START] 准备从数据库加载最后处理时间...")
        self._load_last_time_from_db()
        logger.info(
            f"[START] 加载完成，当前fallback_last_time={self._fallback_last_time}"
        )

        # 5. 启动轮询任务和定时检查任务
        self.is_running = True
        self.polling_task = asyncio.create_task(self._polling_loop())
        self.key_check_task = asyncio.create_task(self._key_check_loop())
        logger.info(f"[START] 轮询任务和定时检查任务已启动: {self.adapter_name}")

        # 6. 启动热度任务调度器
        await self._start_heat_scheduler()

    async def stop(self):
        """停止适配器"""
        logger.info(f"停止葫芦侠适配器: {self.adapter_name}")

        self.is_running = False

        # 停止轮询任务和定时检查任务
        if self.polling_task:
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
            self.polling_task = None

        if hasattr(self, "key_check_task") and self.key_check_task:
            self.key_check_task.cancel()
            try:
                await self.key_check_task
            except asyncio.CancelledError:
                pass
            self.key_check_task = None

        # 停止热度任务调度器
        await self._stop_heat_scheduler()

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

    def _save_login_info_to_db(
        self, user_id: str, key: str, key_expires_at: datetime
    ) -> None:
        """保存登录信息到数据库"""
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

            # 更新登录信息
            state.user_id = user_id
            state.key = key
            state.key_expires_at = key_expires_at
            state.updated_at = datetime.now()

            session.commit()

            logger.info(
                f"[SAVE_LOGIN] 登录信息已保存到数据库: user_id={user_id}, key_expires_at={key_expires_at}"
            )
        except Exception as e:
            logger.error(f"[SAVE_LOGIN] 保存登录信息失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            if session:
                session.rollback()
        finally:
            if session:
                session.close()

    def _load_login_info_from_db(self) -> Optional[Dict[str, Any]]:
        """从数据库加载登录信息"""
        session = None
        try:
            session = self._db_manager.get_session()
            state = (
                session.query(HuluxiaAdapterState)
                .filter_by(adapter_name=self.adapter_name)
                .first()
            )

            if state and state.user_id and state.key:
                self.user_id = state.user_id
                self._key = state.key
                if self.api_client:
                    self.api_client._key = state.key

                logger.info(
                    f"[LOAD_LOGIN] 从数据库加载登录信息: user_id={self.user_id}, key_expires_at={state.key_expires_at}"
                )
                return {
                    "user_id": state.user_id,
                    "key": state.key,
                    "key_expires_at": state.key_expires_at,
                }
            else:
                logger.info("[LOAD_LOGIN] 数据库中没有登录信息")
                return None
        except Exception as e:
            logger.error(f"[LOAD_LOGIN] 加载登录信息失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            if session:
                session.rollback()
        finally:
            if session:
                session.close()

        return None

    async def _relogin(self) -> bool:
        """重新登录"""
        try:
            logger.info("[RELOGIN] 开始重新登录...")
            login_result = await self.api_client.login(
                account=self.config.account,
                password=self.config.password,
                login_main_site=(self.config.market_id == "tool_web"),
            )

            self.user_id = login_result["user_id"]
            self._key = login_result["key"]
            if self.api_client:
                self.api_client._key = login_result["key"]

            # 保存登录信息到数据库
            key_expires_at = datetime.now() + timedelta(days=10)
            self._save_login_info_to_db(self.user_id, self._key, key_expires_at)

            logger.info(
                f"[RELOGIN] 重新登录成功: 用户ID={self.user_id}, key过期时间={key_expires_at}"
            )
            return True
        except Exception as e:
            logger.error(f"[RELOGIN] 重新登录失败: {e}")
            return False

    async def _key_check_loop(self):
        """定时检查key有效性的后台任务"""
        logger.info(f"[KEY_CHECK] 开始定时检查key有效性: {self.adapter_name}")

        while self.is_running:
            try:
                # 解析配置的检查时间
                try:
                    hour, minute = map(int, self.config.key_check_time.split(":"))
                except Exception as e:
                    logger.warning(
                        f"[KEY_CHECK] 配置的检查时间格式错误: {self.config.key_check_time}, 使用默认值 03:00"
                    )
                    hour, minute = 3, 0

                now = datetime.now()
                # 计算下一次检查时间
                next_check = now.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                # 添加60秒缓冲时间，避免边界问题
                if now >= next_check + timedelta(seconds=60):
                    # 今天已经过了检查时间（加上缓冲），检查明天
                    next_check = next_check + timedelta(days=1)

                wait_seconds = (next_check - now).total_seconds()
                logger.info(
                    f"[KEY_CHECK] 下次检查时间: {next_check}, 等待 {wait_seconds:.0f} 秒"
                )

                await asyncio.sleep(wait_seconds)

                # 检查是否还在运行
                if not self.is_running:
                    break

                # 执行检查
                logger.info("[KEY_CHECK] 开始检查key有效性...")
                key_valid = await self.api_client.verify_login_status()
                if key_valid:
                    logger.info("[KEY_CHECK] Key有效，无需重新登录")
                else:
                    logger.warning("[KEY_CHECK] Key无效，尝试重新登录")
                    success = await self._relogin()
                    if not success:
                        logger.error("[KEY_CHECK] 重新登录失败，等待下次重试")

            except asyncio.CancelledError:
                logger.info(f"[KEY_CHECK] 定时检查任务被取消: {self.adapter_name}")
                break
            except Exception as e:
                logger.error(f"[KEY_CHECK] 定时检查异常: {e}")
                import traceback

                logger.error(traceback.format_exc())
                if self.is_running:
                    # 出错后等待1小时再重试
                    await asyncio.sleep(3600)

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
        logger.info(f"[POLLING] 开始轮询: {self.adapter_name}")

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
                    response_msg = response.get("msg", "")
                    logger.warning(
                        f"获取消息失败: status={response.get('status')}, "
                        f"msg={response_msg}, "
                        f"response={response}"
                    )

                    # 检测"未登录"，尝试重新登录
                    if "未登录" in response_msg:
                        logger.warning("[POLLING] 检测到未登录，尝试重新登录...")
                        relogin_success = await self._relogin()
                        if not relogin_success:
                            # 重新登录失败，跳过本次轮询
                            logger.error("[POLLING] 重新登录失败，跳过本次轮询")
                            await asyncio.sleep(self.config.poll_interval)
                            continue
                        # 重新登录成功，重新获取消息
                        continue

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
                        comment_id = content.get("commentID")

                        logger.info(
                            f"[POLLING] 处理消息: create_time={create_time}, user={content.get('user', {}).get('nick')}, text={text[:30]}..."
                        )

                        # 转换消息
                        im_message = await self.convert_to_message(msg_item)

                        # 分发消息
                        logger.info(f"[POLLING] 开始分发消息到工作流...")
                        await self.dispatcher.dispatch(self, im_message)
                        logger.info(f"[POLLING] 消息分发完成，comment_id={comment_id}")

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

                # 6. 等待下一次轮询
                await asyncio.sleep(self.config.poll_interval)

            except asyncio.CancelledError:
                logger.info(f"[POLLING] 轮询任务被取消: {self.adapter_name}")
                break
            except Exception as e:
                logger.error(f"[POLLING] 轮询异常: {e}")
                import traceback

                logger.error(traceback.format_exc())
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
        except Exception as e:
            logger.error(f"[DB_SAVE] 保存时间戳到数据库失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            if session:
                session.rollback()
        finally:
            if session:
                session.close()

    # ===== 热度任务调度器管理 =====

    async def _start_heat_scheduler(self):
        """启动热度任务调度器"""
        try:
            # 创建执行器
            executor = HeatTaskExecutor(
                api_client=self.api_client,
                delay_min_ms=self.config.heat.delay_min_ms,
                delay_max_ms=self.config.heat.delay_max_ms,
            )

            # 创建调度器
            self._heat_scheduler = HeatTaskScheduler(
                config=self.config.heat, api_client=self.api_client, executor=executor
            )

            # 启动调度器
            await self._heat_scheduler.start()

        except Exception as e:
            logger.error(f"[HEAT] 启动热度调度器失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            if self.config.heat_enable:
                raise RuntimeError(f"热度功能已启用但调度器启动失败: {e}") from e
            self._heat_scheduler = None

    async def _stop_heat_scheduler(self):
        """停止热度任务调度器"""
        if self._heat_scheduler:
            try:
                await self._heat_scheduler.stop()
                logger.info(f"[HEAT] 热度调度器已停止: {self.adapter_name}")
            except Exception as e:
                logger.error(f"[HEAT] 停止热度调度器失败: {e}")
            finally:
                self._heat_scheduler = None
