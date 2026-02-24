import asyncio
from dataclasses import dataclass, field
from typing import Dict, List

from kirara_ai.logger import get_logger
from ..api_client import HuluxiaApiClient

logger = get_logger("FollowCheckExecutor")


@dataclass
class FollowCheckResult:
    """互关检查执行结果"""

    total_checked: int = 0
    total_unfollowed: int = 0
    failed_unfollows: int = 0
    user_details: List[Dict] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """取消关注成功率"""
        if self.total_unfollowed + self.failed_unfollows == 0:
            return 100.0
        return (
            self.total_unfollowed / (self.total_unfollowed + self.failed_unfollows)
        ) * 100


class FollowCheckExecutor:
    """
    互关检查任务执行器

    负责：
    - 获取关注列表
    - 检查关注状态
    - 取消未回关的用户关注
    """

    def __init__(
        self,
        api_client: HuluxiaApiClient,
        user_id: str,
        unfollow_delay_ms: int = 1200,
    ):
        """
        初始化执行器

        Args:
            api_client: API客户端实例
            user_id: 当前登录用户的ID
            unfollow_delay_ms: 取消关注操作之间的延迟时间（毫秒），默认1.2秒
        """
        self.api_client = api_client
        self.user_id = user_id
        self.unfollow_delay_ms = unfollow_delay_ms

    async def execute(self) -> FollowCheckResult:
        """
        执行互关检查任务

        遍历关注列表，对于 friendship=1 的用户（已关注但未回关），取消关注

        Returns:
            FollowCheckResult: 执行结果统计
        """
        result = FollowCheckResult()

        logger.info(f"[EXECUTOR] 开始执行互关检查任务，用户ID: {self.user_id}")

        start = 0
        page = 0

        while True:
            page += 1
            logger.info(f"[EXECUTOR] 正在获取第 {page} 页关注列表，start={start}")

            try:
                response = await self.api_client.get_following_list(
                    user_id=self.user_id, start=start, count=20
                )

                if response.get("status") != 1:
                    msg = response.get("msg", "未知错误")
                    logger.error(f"[EXECUTOR] 获取关注列表失败: {msg}")
                    break

                friendships = response.get("friendships", [])
                if not friendships:
                    logger.info("[EXECUTOR] 关注列表为空，结束检查")
                    break

                for item in friendships:
                    result.total_checked += 1
                    user_info = item.get("user", {})
                    target_user_id = str(user_info.get("userID", ""))
                    nick = user_info.get("nick", "")
                    friendship = item.get("friendship", 0)

                    if friendship == 1:
                        logger.info(
                            f"[EXECUTOR] 发现未回关用户: {nick}(ID:{target_user_id})，准备取消关注"
                        )

                        try:
                            unfollow_response = await self.api_client.unfollow_user(
                                target_user_id
                            )

                            if unfollow_response.get("status") == 1:
                                result.total_unfollowed += 1
                                result.user_details.append(
                                    {
                                        "user_id": target_user_id,
                                        "nick": nick,
                                        "action": "unfollowed",
                                        "success": True,
                                    }
                                )
                                logger.info(
                                    f"[EXECUTOR] 成功取消关注: {nick}(ID:{target_user_id})"
                                )
                            else:
                                result.failed_unfollows += 1
                                error_msg = unfollow_response.get("msg", "未知错误")
                                result.user_details.append(
                                    {
                                        "user_id": target_user_id,
                                        "nick": nick,
                                        "action": "unfollow_failed",
                                        "success": False,
                                        "error": error_msg,
                                    }
                                )
                                logger.warning(
                                    f"[EXECUTOR] 取消关注失败: {nick}(ID:{target_user_id}), 原因: {error_msg}"
                                )

                            await asyncio.sleep(self.unfollow_delay_ms / 1000)

                        except Exception as e:
                            result.failed_unfollows += 1
                            result.user_details.append(
                                {
                                    "user_id": target_user_id,
                                    "nick": nick,
                                    "action": "unfollow_error",
                                    "success": False,
                                    "error": str(e),
                                }
                            )
                            logger.error(
                                f"[EXECUTOR] 取消关注异常: {nick}(ID:{target_user_id}), 错误: {e}"
                            )

                more = response.get("more", 0)
                if more != 1:
                    logger.info("[EXECUTOR] 已遍历完所有关注列表")
                    break

                next_start = response.get("start")
                if next_start is None:
                    logger.info("[EXECUTOR] 没有下一页标记，结束遍历")
                    break

                try:
                    start = int(next_start)
                except (ValueError, TypeError):
                    start = start + len(friendships)

            except Exception as e:
                logger.error(f"[EXECUTOR] 获取关注列表异常: {e}")
                import traceback

                logger.error(traceback.format_exc())
                break

        logger.info(
            f"[EXECUTOR] 互关检查任务完成: 检查 {result.total_checked} 人, "
            f"取消关注 {result.total_unfollowed} 人, "
            f"失败 {result.failed_unfollows} 人"
        )

        return result
