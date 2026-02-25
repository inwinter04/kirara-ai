import asyncio
import random
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from kirara_ai.database.manager import DatabaseManager
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.model_types import LLMAbility, ModelType
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.logger import get_logger

from ..api_client import HuluxiaApiClient
from ..models import HuluxiaAdapterState
from ..utils import clean_unsupported_emoticons

logger = get_logger("PoolTaskExecutor")

POOL_CAT_ID = 2
MAX_PAGES = 5
PAGE_SIZE = 20
COMMENT_DELAY_MIN_MS = 10000
COMMENT_DELAY_MAX_MS = 80000
MAX_LLM_RETRIES = 3
RETRY_DELAY_BASE = 2.0

POOL_COMMENT_PROMPT = """# Role: 角色扮演

## 基本信息
- 姓名: 苏遥

## 性格特点
- 表面懒散，内心细腻
- 有点毒舌但很真诚，讨厌虚伪客套，喜欢直球交流

## 说话风格
- 活泼开朗
- 日常像朋友聊天
- 喜欢聊独立游戏、广州老城区探店

### 使用表情包
聊天中可以加入一些合适的表情包：
[哈哈][吐舌][啊][酷][怒][开心][汗][泪][真棒][疑问][阴险][咦][委屈][花心][呼~][笑眼][冷][太开心][滑稽][勉强][狂汗][乖][睡觉][惊哭][升起][惊讶][喷][爱心][心碎][玫瑰][礼物][彩虹][星星月亮][太阳][铅笔][灯泡][茶杯][蛋糕][音乐][haha][胜利][大拇指][弱][OK][赖皮][感动][十分惊讶][怒气][哭泣][吃惊][嘲弄][飘过][转圈哭][神经病][揪耳朵][惊汗][隐身][不要嘛][遁][不公平][爬来了][蛋花哭][温柔][点头][撒钱][献花][寒]

---

现在有一位用户在葫芦侠社区发了一个帖子，请你以苏遥的身份回复评论：

**帖子作者**：{author_name}
**帖子标题**：{title}
**帖子内容**：{detail}

要求：
1. 回复要自然、有趣，像朋友聊天一样
2. 长度15-80字
3. 可以使用合适的表情包/emoji

请直接输出评论内容，不要有任何其他说明或标记。"""


@dataclass
class PoolPost:
    """泳池帖子数据"""

    post_id: int
    title: str
    detail: str
    create_time: int
    author_name: str


@dataclass
class PoolExecutionResult:
    """泳池灌水任务执行结果"""

    total_posts: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0


class PoolTaskExecutor:
    """
    泳池灌水任务执行器

    负责：
    - 获取泳池板块帖子列表
    - 调用LLM生成评论
    - 发送评论
    - 更新处理进度
    """

    def __init__(
        self,
        api_client: HuluxiaApiClient,
        db_manager: DatabaseManager,
        llm_manager: LLMManager,
        adapter_name: str,
        _key: str,
        market_id: str,
    ):
        """
        初始化执行器

        Args:
            api_client: API客户端实例
            db_manager: 数据库管理器
            llm_manager: LLM管理器
            adapter_name: 适配器名称
            _key: 登录凭证
            market_id: 市场ID
        """
        self.api_client = api_client
        self.db_manager = db_manager
        self.llm_manager = llm_manager
        self.adapter_name = adapter_name
        self._key = _key
        self.market_id = market_id

    async def execute(self) -> PoolExecutionResult:
        """
        执行泳池灌水任务

        Returns:
            PoolExecutionResult: 执行结果统计
        """
        logger.info("=" * 60)
        logger.info("[POOL_EXECUTOR] ========== 开始执行泳池灌水任务 ==========")
        logger.info("=" * 60)

        result = PoolExecutionResult()

        try:
            last_create_time = self._load_last_pool_post_create_time()
            logger.info(f"[POOL_EXECUTOR] 上次处理的帖子创建时间: {last_create_time}")

            all_posts = await self._fetch_all_posts(last_create_time)
            result.skipped_count = len(all_posts.get("skipped", []))
            new_posts = all_posts.get("new", [])

            if not new_posts:
                logger.info("[POOL_EXECUTOR] 没有新帖子需要处理")
                return result

            logger.info(f"[POOL_EXECUTOR] 获取到 {len(new_posts)} 个新帖子")

            new_posts.sort(key=lambda x: x.create_time)

            for post in new_posts:
                try:
                    comment_text = await self._generate_comment(post)
                    if not comment_text:
                        logger.warning(
                            f"[POOL_EXECUTOR] 生成评论失败，跳过帖子: {post.post_id}"
                        )
                        result.failed_count += 1
                        continue

                    await self._send_comment(post.post_id, comment_text)
                    result.success_count += 1
                    result.total_posts += 1

                    self._save_last_pool_post_create_time(post.create_time)

                    delay_seconds = (
                        random.randint(COMMENT_DELAY_MIN_MS, COMMENT_DELAY_MAX_MS)
                        / 1000
                    )
                    logger.info(
                        f"[POOL_EXECUTOR] 等待 {delay_seconds:.1f} 秒后处理下一个帖子"
                    )
                    await asyncio.sleep(delay_seconds)

                except Exception as e:
                    logger.error(
                        f"[POOL_EXECUTOR] 处理帖子失败: post_id={post.post_id}, error={e}"
                    )
                    result.failed_count += 1
                    result.total_posts += 1
                    import traceback

                    logger.error(traceback.format_exc())

        except Exception as e:
            logger.error(f"[POOL_EXECUTOR] 执行任务异常: {e}")
            import traceback

            logger.error(traceback.format_exc())

        logger.info("=" * 60)
        logger.info(f"[POOL_EXECUTOR] ========== 泳池灌水任务完成 ==========")
        logger.info(f"[POOL_EXECUTOR] 处理帖子数: {result.total_posts}")
        logger.info(f"[POOL_EXECUTOR] 成功: {result.success_count}")
        logger.info(f"[POOL_EXECUTOR] 失败: {result.failed_count}")
        logger.info(f"[POOL_EXECUTOR] 跳过(已处理): {result.skipped_count}")
        logger.info("=" * 60)

        return result

    async def _fetch_all_posts(self, last_create_time: int) -> dict:
        """
        获取所有新帖子（最多5页）

        Args:
            last_create_time: 上次处理的帖子创建时间

        Returns:
            dict: {"new": [PoolPost], "skipped": [PoolPost]}
        """
        new_posts = []
        skipped_posts = []
        start = 0

        for page in range(MAX_PAGES):
            logger.info(
                f"[POOL_EXECUTOR] 获取第 {page + 1}/{MAX_PAGES} 页帖子, start={start}"
            )

            try:
                response = await self.api_client.get_pool_post_list(
                    start=start, count=PAGE_SIZE
                )

                if response.get("status") != 1:
                    logger.warning(
                        f"[POOL_EXECUTOR] 获取帖子失败: {response.get('msg')}"
                    )
                    break

                posts = response.get("posts", [])
                if not posts:
                    logger.info("[POOL_EXECUTOR] 没有更多帖子")
                    break

                for post_data in posts:
                    post = self._parse_post(post_data)
                    if post.create_time <= last_create_time:
                        skipped_posts.append(post)
                    else:
                        new_posts.append(post)

                should_stop = False
                for post_data in posts:
                    if post_data.get("createTime", 0) <= last_create_time:
                        should_stop = True
                        break

                if should_stop:
                    logger.info("[POOL_EXECUTOR] 检测到已处理帖子，停止翻页")
                    break

                more = response.get("more", 0)
                if more != 1:
                    logger.info("[POOL_EXECUTOR] 没有更多帖子（more=0）")
                    break

                start = response.get("start", 0)
                if not start:
                    start = posts[-1].get("createTime", 0) if posts else 0

            except Exception as e:
                logger.error(f"[POOL_EXECUTOR] 获取帖子列表异常: {e}")
                break

        return {"new": new_posts, "skipped": skipped_posts}

    def _is_retryable_error(self, error: Exception) -> bool:
        """判断LLM请求错误是否可重试"""
        if isinstance(error, (ConnectionError, TimeoutError)):
            return True

        if hasattr(error, "response") and error.response is not None:
            status = getattr(error.response, "status_code", None)
            if status:
                return status == 429 or 500 <= status < 600

        return False

    def _parse_post(self, post_data: dict) -> PoolPost:
        """
        解析帖子数据

        Args:
            post_data: API返回的帖子数据

        Returns:
            PoolPost: 解析后的帖子对象
        """
        user = post_data.get("user", {})
        author_name = user.get("nick") if user and user.get("nick") else "匿名用户"

        return PoolPost(
            post_id=post_data.get("postID", 0),
            title=post_data.get("title", ""),
            detail=post_data.get("detail", ""),
            create_time=post_data.get("createTime", 0),
            author_name=author_name,
        )

    async def _generate_comment(self, post: PoolPost) -> Optional[str]:
        """
        调用LLM生成评论（带重试机制）

        Args:
            post: 帖子对象

        Returns:
            生成的评论文本，失败返回None
        """
        model_id = self.llm_manager.get_models_by_ability(
            ModelType.LLM, LLMAbility.TextChat
        )
        if not model_id:
            logger.error("[POOL_EXECUTOR] 没有可用的LLM模型")
            return None

        llm = self.llm_manager.get_llm(model_id)
        if not llm:
            logger.error(f"[POOL_EXECUTOR] 获取LLM适配器失败: model_id={model_id}")
            return None

        prompt = POOL_COMMENT_PROMPT.format(
            author_name=post.author_name,
            title=post.title,
            detail=post.detail,
        )

        messages = [
            LLMChatMessage(role="user", content=[LLMChatTextContent(text=prompt)])
        ]

        request = LLMChatRequest(
            messages=messages,
            model=model_id,
        )

        logger.info(
            f"[POOL_EXECUTOR] 调用LLM生成评论: model={model_id}, post_id={post.post_id}"
        )

        for attempt in range(MAX_LLM_RETRIES + 1):
            try:
                if attempt > 0:
                    base_delay = RETRY_DELAY_BASE * (2 ** (attempt - 1))
                    jitter = random.uniform(0, base_delay * 0.1)
                    delay = base_delay + jitter
                    logger.warning(
                        f"[POOL_EXECUTOR] LLM请求重试 ({attempt}/{MAX_LLM_RETRIES + 1})，等待 {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)

                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda: llm.chat(request))

                if not response or not response.message or not response.message.content:
                    logger.warning("[POOL_EXECUTOR] LLM返回空响应")
                    return None

                comment_text = ""
                for content in response.message.content:
                    if hasattr(content, "text"):
                        comment_text += content.text

                comment_text = comment_text.strip()
                if not comment_text:
                    logger.warning("[POOL_EXECUTOR] LLM返回的评论为空")
                    return None

                logger.info(f"[POOL_EXECUTOR] 生成评论成功: {comment_text[:50]}...")
                return comment_text

            except Exception as e:
                is_retryable = self._is_retryable_error(e)

                if attempt < MAX_LLM_RETRIES and is_retryable:
                    error_type = (
                        "429 速率限制"
                        if hasattr(e, "response")
                        and getattr(e.response, "status_code", None) == 429
                        else str(type(e).__name__)
                    )
                    logger.warning(
                        f"[POOL_EXECUTOR] LLM请求失败 ({attempt + 1}/{MAX_LLM_RETRIES + 1}): {error_type}"
                    )
                    continue

                logger.error(f"[POOL_EXECUTOR] 生成评论异常: {e}")
                import traceback

                logger.error(traceback.format_exc())
                return None

    async def _send_comment(self, post_id: int, text: str):
        """
        发送评论

        Args:
            post_id: 帖子ID
            text: 评论内容
        """
        try:
            # 清理不支持的表情
            # 注意：泳池灌水功能中 LLM 处于可控状态，不会输出 Markdown 格式，
            # 因此无需调用 clean_markdown()，避免增加不必要的复杂度
            original_text = text
            text = clean_unsupported_emoticons(text)
            if text != original_text:
                logger.debug(
                    f"[POOL_EXECUTOR] 不支持的表情已清理: '{original_text}' -> '{text}'"
                )

            response = await self.api_client.create_comment(
                _key=self._key,
                market_id=self.market_id,
                post_id=post_id,
                text=text,
                comment_id=0,
                images="",
            )

            response_msg = response.get("msg", "")
            response_status = response.get("status", 0)

            if response_status == 1:
                logger.info(f"[POOL_EXECUTOR] 评论发送成功: post_id={post_id}")
            elif "未登录" in response_msg:
                logger.warning(f"[POOL_EXECUTOR] 发送评论失败：未登录")
                raise Exception("未登录，需要重新登录")
            elif "重复评论" in response_msg:
                logger.warning(f"[POOL_EXECUTOR] 发送评论失败：重复评论")
            else:
                logger.warning(
                    f"[POOL_EXECUTOR] 评论发送可能失败: post_id={post_id}, msg={response_msg}"
                )

        except Exception as e:
            logger.error(f"[POOL_EXECUTOR] 发送评论异常: post_id={post_id}, error={e}")
            raise

    def _load_last_pool_post_create_time(self) -> int:
        """
        从数据库加载最后处理的帖子创建时间

        Returns:
            最后处理的帖子创建时间（毫秒时间戳），如果没有则返回0
        """
        session = None
        try:
            session = self.db_manager.get_session()
            state = (
                session.query(HuluxiaAdapterState)
                .filter_by(adapter_name=self.adapter_name)
                .first()
            )

            if state and state.last_pool_post_create_time:
                return state.last_pool_post_create_time
            return 0

        except Exception as e:
            logger.error(f"[POOL_EXECUTOR] 加载最后处理时间失败: {e}")
            return 0
        finally:
            if session:
                session.close()

    def _save_last_pool_post_create_time(self, create_time: int):
        """
        保存最后处理的帖子创建时间到数据库

        Args:
            create_time: 帖子创建时间（毫秒时间戳）
        """
        session = None
        try:
            session = self.db_manager.get_session()

            state = (
                session.query(HuluxiaAdapterState)
                .filter_by(adapter_name=self.adapter_name)
                .first()
            )
            if not state:
                state = HuluxiaAdapterState(adapter_name=self.adapter_name)
                session.add(state)

            current_time = state.last_pool_post_create_time or 0
            state.last_pool_post_create_time = max(current_time, create_time)
            state.updated_at = datetime.now()

            session.commit()
            logger.debug(
                f"[POOL_EXECUTOR] 保存最后处理时间: {state.last_pool_post_create_time}"
            )

        except Exception as e:
            logger.error(f"[POOL_EXECUTOR] 保存最后处理时间失败: {e}")
            if session:
                session.rollback()
        finally:
            if session:
                session.close()
