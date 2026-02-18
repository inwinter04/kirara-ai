import asyncio
from datetime import datetime
from typing import Optional

from kirara_ai.logger import get_logger

from ..models import HuluxiaPoolConfig
from .executor import PoolTaskExecutor

logger = get_logger("PoolTaskScheduler")


class PoolTaskScheduler:
    """
    泳池灌水任务调度器

    负责：
    - 定时调度管理
    - 委托执行器执行任务
    - 记录执行日志
    """

    def __init__(self, config: HuluxiaPoolConfig, executor: PoolTaskExecutor):
        """
        初始化调度器

        Args:
            config: 泳池灌水功能配置
            executor: 任务执行器实例
        """
        self.config = config
        self.executor = executor
        self.is_running = False
        self._scheduler_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动调度器"""
        if not self.config.enable:
            logger.info("[POOL_SCHEDULER] 泳池灌水功能未启用")
            return

        if self.is_running:
            logger.warning("[POOL_SCHEDULER] 调度器已在运行")
            return

        self.is_running = True
        self._scheduler_task = asyncio.create_task(self._schedule_loop())

        logger.info(
            f"[POOL_SCHEDULER] 调度器已启动，检查间隔: {self.config.interval_minutes} 分钟"
        )

    async def stop(self):
        """停止调度器"""
        if not self.is_running:
            return

        logger.info("[POOL_SCHEDULER] 正在停止调度器...")
        self.is_running = False

        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

        logger.info("[POOL_SCHEDULER] 调度器已停止")

    async def _schedule_loop(self):
        """调度循环"""
        logger.info("[POOL_SCHEDULER] 调度循环已启动")

        # 启动后1分钟立即执行一次
        initial_delay = 60
        logger.info(f"[POOL_SCHEDULER] 启动后 {initial_delay} 秒执行首次任务")
        await asyncio.sleep(initial_delay)

        if not self.is_running:
            logger.info("[POOL_SCHEDULER] 调度器已停止，退出循环")
            return

        await self._execute_task()

        while self.is_running:
            try:
                interval_seconds = self.config.interval_minutes * 60
                next_run_time = datetime.now()
                next_run_time = next_run_time.replace(second=0, microsecond=0)
                from datetime import timedelta

                next_run_time = next_run_time + timedelta(seconds=interval_seconds)

                logger.info(
                    f"[POOL_SCHEDULER] 下次执行时间: {next_run_time.strftime('%Y-%m-%d %H:%M')}, "
                    f"等待 {self.config.interval_minutes} 分钟"
                )

                await asyncio.sleep(interval_seconds)

                if not self.is_running:
                    logger.info("[POOL_SCHEDULER] 调度器已停止，退出循环")
                    break

                await self._execute_task()

            except asyncio.CancelledError:
                logger.info("[POOL_SCHEDULER] 调度循环被取消")
                break
            except Exception as e:
                logger.error(f"[POOL_SCHEDULER] 调度异常: {e}")
                import traceback

                logger.error(traceback.format_exc())

                if self.is_running:
                    logger.info("[POOL_SCHEDULER] 等待10分钟后重试...")
                    await asyncio.sleep(600)

        logger.info("[POOL_SCHEDULER] 调度循环已结束")

    async def _execute_task(self):
        """执行任务"""
        logger.info("=" * 60)
        logger.info("[POOL_SCHEDULER] ========== 开始执行泳池灌水任务 ==========")
        logger.info(
            f"[POOL_SCHEDULER] 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info("=" * 60)

        started_at = datetime.now()

        try:
            result = await self.executor.execute()

            elapsed = (datetime.now() - started_at).total_seconds()

            logger.info("=" * 60)
            logger.info("[POOL_SCHEDULER] ========== 泳池灌水任务执行完成 ==========")
            logger.info(f"[POOL_SCHEDULER] 处理帖子数: {result.total_posts}")
            logger.info(f"[POOL_SCHEDULER] 成功: {result.success_count}")
            logger.info(f"[POOL_SCHEDULER] 失败: {result.failed_count}")
            logger.info(f"[POOL_SCHEDULER] 跳过(已处理): {result.skipped_count}")
            logger.info(f"[POOL_SCHEDULER] 总耗时: {elapsed:.1f} 秒")
            logger.info("=" * 60)

        except Exception as e:
            logger.error("=" * 60)
            logger.error("[POOL_SCHEDULER] ========== 泳池灌水任务执行失败 ==========")
            logger.error(f"[POOL_SCHEDULER] 错误: {e}")
            import traceback

            logger.error(traceback.format_exc())
            logger.error("=" * 60)
