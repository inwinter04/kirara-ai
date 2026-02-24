import asyncio
from datetime import datetime, timedelta
from typing import Optional

from kirara_ai.logger import get_logger
from ..models import HuluxiaFollowCheckConfig
from .executor import FollowCheckExecutor, FollowCheckResult

logger = get_logger("FollowCheckScheduler")


class FollowCheckScheduler:
    """
    互关检查任务调度器

    负责：
    - 定时调度管理
    - 计算下次执行时间
    - 委托执行器执行任务
    - 记录执行日志
    """

    def __init__(
        self,
        config: HuluxiaFollowCheckConfig,
        api_client,
        executor: FollowCheckExecutor,
    ):
        """
        初始化调度器

        Args:
            config: 互关检查功能配置
            api_client: API客户端实例
            executor: 任务执行器实例
        """
        self.config = config
        self.api_client = api_client
        self.executor = executor
        self.is_running = False
        self._scheduler_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动调度器"""
        if not self.config.enable:
            logger.info("[SCHEDULER] 互关检查功能未启用")
            return

        if self.is_running:
            logger.warning("[SCHEDULER] 调度器已在运行")
            return

        self.is_running = True
        self._scheduler_task = asyncio.create_task(self._schedule_loop())

        logger.info(f"[SCHEDULER] 互关检查调度器已启动，执行时间: {self.config.time}")

    async def stop(self):
        """停止调度器"""
        if not self.is_running:
            return

        logger.info("[SCHEDULER] 正在停止互关检查调度器...")
        self.is_running = False

        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

        logger.info("[SCHEDULER] 互关检查调度器已停止")

    async def _schedule_loop(self):
        """调度循环"""
        logger.info("[SCHEDULER] 互关检查调度循环已启动")

        while self.is_running:
            try:
                wait_seconds = self._calculate_wait_seconds()
                next_run_time = datetime.now() + timedelta(seconds=wait_seconds)

                logger.info(
                    f"[SCHEDULER] 下次执行互关检查: {self.config.time} "
                    f"({next_run_time.strftime('%Y-%m-%d %H:%M')}) "
                    f"等待 {wait_seconds / 3600:.1f} 小时"
                )

                await asyncio.sleep(wait_seconds)

                if not self.is_running:
                    logger.info("[SCHEDULER] 调度器已停止，退出循环")
                    break

                await self._execute_task()

            except asyncio.CancelledError:
                logger.info("[SCHEDULER] 调度循环被取消")
                break
            except Exception as e:
                logger.error(f"[SCHEDULER] 调度异常: {e}")
                import traceback

                logger.error(traceback.format_exc())

                if self.is_running:
                    logger.info("[SCHEDULER] 等待1小时后重试...")
                    await asyncio.sleep(3600)

        logger.info("[SCHEDULER] 互关检查调度循环已结束")

    async def _execute_task(self):
        """执行互关检查任务"""
        logger.info("=" * 60)
        logger.info(f"[SCHEDULER] ========== 开始执行互关检查任务 ==========")
        logger.info(f"[SCHEDULER] 执行时间: {self.config.time}")
        logger.info("=" * 60)

        started_at = datetime.now()

        try:
            result: FollowCheckResult = await self.executor.execute()

            elapsed = (datetime.now() - started_at).total_seconds()

            logger.info("=" * 60)
            logger.info(f"[SCHEDULER] ========== 互关检查任务执行完成 ==========")
            logger.info(f"[SCHEDULER] 检查用户数: {result.total_checked}")
            logger.info(f"[SCHEDULER] 取消关注数: {result.total_unfollowed}")
            logger.info(f"[SCHEDULER] 失败数: {result.failed_unfollows}")

            if result.total_unfollowed + result.failed_unfollows > 0:
                logger.info(f"[SCHEDULER] 成功率: {result.success_rate:.1f}%")

            logger.info(f"[SCHEDULER] 总耗时: {elapsed:.1f} 秒")

            if result.user_details:
                logger.info("[SCHEDULER] 操作详情:")
                for detail in result.user_details:
                    status = (
                        "成功"
                        if detail.get("success")
                        else f"失败({detail.get('error', '未知')})"
                    )
                    logger.info(
                        f"  - {detail.get('nick')}(ID:{detail.get('user_id')}): {status}"
                    )

            logger.info("=" * 60)

        except Exception as e:
            logger.error("=" * 60)
            logger.error(f"[SCHEDULER] ========== 互关检查任务执行失败 ==========")
            logger.error(f"[SCHEDULER] 错误: {e}")
            import traceback

            logger.error(traceback.format_exc())
            logger.error("=" * 60)

    def _calculate_wait_seconds(self) -> float:
        """
        计算距离执行时间的等待秒数

        Returns:
            等待秒数
        """
        now = datetime.now()
        hour, minute = map(int, self.config.time.split(":"))

        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if now >= next_run:
            next_run = next_run + timedelta(days=1)

        wait_seconds = (next_run - now).total_seconds()
        return wait_seconds
