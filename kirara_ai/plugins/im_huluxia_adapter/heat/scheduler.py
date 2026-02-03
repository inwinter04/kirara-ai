import asyncio
from datetime import datetime, timedelta
from typing import Optional

from kirara_ai.logger import get_logger
from ..models import HuluxiaHeatConfig, HuluxiaHeatSchedule
from .executor import HeatTaskExecutor, HeatExecutionResult

logger = get_logger("HeatTaskScheduler")


class HeatTaskScheduler:
    """
    热度任务调度器

    负责：
    - 定时调度管理
    - 计算下次执行时间
    - 委托执行器执行任务
    - 记录执行日志
    """

    def __init__(
        self, config: HuluxiaHeatConfig, api_client, executor: HeatTaskExecutor
    ):
        """
        初始化调度器

        Args:
            config: 热度功能配置
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
        if not self.config.enable or not self.config.schedules:
            logger.info("[SCHEDULER] 热度功能未启用或无调度配置")
            return

        if self.is_running:
            logger.warning("[SCHEDULER] 调度器已在运行")
            return

        self.is_running = True
        self._scheduler_task = asyncio.create_task(self._schedule_loop())

        schedule_count = len(self.config.schedules)
        schedules_str = ", ".join(
            [f"{s.time}({s.cat_ids})" for s in self.config.schedules]
        )
        logger.info(
            f"[SCHEDULER] 调度器已启动，共 {schedule_count} 个计划: {schedules_str}"
        )

    async def stop(self):
        """停止调度器"""
        if not self.is_running:
            return

        logger.info("[SCHEDULER] 正在停止调度器...")
        self.is_running = False

        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

        logger.info("[SCHEDULER] 调度器已停止")

    async def _schedule_loop(self):
        """调度循环"""
        logger.info("[SCHEDULER] 调度循环已启动")

        while self.is_running:
            try:
                # 1. 找到下一个调度计划
                next_schedule = self._find_next_schedule()

                if not next_schedule:
                    # 没有找到有效的调度配置，停止调度器
                    logger.error("[SCHEDULER] 没有找到有效的调度配置，调度器停止运行")
                    break

                # 2. 计算等待时间
                wait_seconds = self._calculate_wait_seconds(next_schedule.time)
                wait_hours = wait_seconds / 3600
                next_run_time = datetime.now() + timedelta(seconds=wait_seconds)

                logger.info(
                    f"[SCHEDULER] 下次执行: {next_schedule.time} "
                    f"板块{next_schedule.cat_ids} "
                    f"({next_run_time.strftime('%Y-%m-%d %H:%M')}) "
                    f"等待 {wait_hours:.1f} 小时"
                )

                # 3. 等待到执行时间
                await asyncio.sleep(wait_seconds)

                # 4. 检查是否还在运行
                if not self.is_running:
                    logger.info("[SCHEDULER] 调度器已停止，退出循环")
                    break

                # 5. 执行调度任务
                await self._execute_schedule(next_schedule)

            except asyncio.CancelledError:
                logger.info("[SCHEDULER] 调度循环被取消")
                break
            except Exception as e:
                logger.error(f"[SCHEDULER] 调度异常: {e}")
                import traceback

                logger.error(traceback.format_exc())

                # 出错后等待10分钟再重试
                if self.is_running:
                    logger.info("[SCHEDULER] 等待10分钟后重试...")
                    await asyncio.sleep(600)

        logger.info("[SCHEDULER] 调度循环已结束")

    async def _execute_schedule(self, schedule: HuluxiaHeatSchedule):
        """
        执行调度任务

        Args:
            schedule: 调度计划配置
        """
        logger.info("=" * 60)
        logger.info(f"[SCHEDULER] ========== 开始执行热度任务 ==========")
        logger.info(f"[SCHEDULER] 计划时间: {schedule.time}")
        logger.info(f"[SCHEDULER] 板块列表: {schedule.cat_ids}")
        logger.info(f"[SCHEDULER] 每板块请求次数: {schedule.request_count}")
        logger.info(
            f"[SCHEDULER] 延迟范围: {self.config.delay_min_ms}-{self.config.delay_max_ms}ms"
        )
        logger.info("=" * 60)

        started_at = datetime.now()

        try:
            # 委托给执行器执行
            result = await self.executor.execute_schedule(schedule)

            # 输出执行结果摘要
            elapsed = (datetime.now() - started_at).total_seconds()

            logger.info("=" * 60)
            logger.info(f"[SCHEDULER] ========== 热度任务执行完成 ==========")
            logger.info(f"[SCHEDULER] 计划时间: {schedule.time}")
            logger.info(f"[SCHEDULER] 总请求数: {result.total_requests}")
            logger.info(f"[SCHEDULER] 成功: {result.total_success}")
            logger.info(f"[SCHEDULER] 失败: {result.total_failed}")

            if result.total_requests > 0:
                success_rate = (result.total_success / result.total_requests) * 100
                logger.info(f"[SCHEDULER] 成功率: {success_rate:.1f}%")

            logger.info(f"[SCHEDULER] 总耗时: {elapsed:.1f} 秒")

            # 输出各板块详细结果
            for cat_id, cat_result in result.cat_results.items():
                logger.info(
                    f"[SCHEDULER] 板块 {cat_id}: "
                    f"成功 {cat_result.success}, "
                    f"失败 {cat_result.failed}, "
                    f"成功率 {cat_result.success_rate:.1f}%"
                )

            logger.info("=" * 60)

        except Exception as e:
            logger.error("=" * 60)
            logger.error(f"[SCHEDULER] ========== 热度任务执行失败 ==========")
            logger.error(f"[SCHEDULER] 计划时间: {schedule.time}")
            logger.error(f"[SCHEDULER] 错误: {e}")
            import traceback

            logger.error(traceback.format_exc())
            logger.error("=" * 60)

    def _find_next_schedule(self) -> Optional[HuluxiaHeatSchedule]:
        """
        找到下一个要执行的调度计划

        Returns:
            下一个调度计划，如果没有则返回None
        """
        if not self.config.schedules:
            return None

        now = datetime.now()
        current_time = now.strftime("%H:%M")

        # 按时间排序
        sorted_schedules = sorted(self.config.schedules, key=lambda x: x.time)

        # 找到今天还未执行的调度
        for schedule in sorted_schedules:
            if schedule.time > current_time:
                return schedule

        # 如果今天的都已执行，返回明天第一个
        if sorted_schedules:
            return sorted_schedules[0]

        return None

    def _calculate_wait_seconds(self, schedule_time: str) -> float:
        """
        计算距离执行时间的等待秒数

        Args:
            schedule_time: 调度时间（HH:MM格式）

        Returns:
            等待秒数
        """
        now = datetime.now()
        hour, minute = map(int, schedule_time.split(":"))

        # 计算今天的执行时间
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # 如果当前时间已经过了今天的调度时间，则调度到明天
        if now >= next_run:
            next_run = next_run + timedelta(days=1)

        # 计算等待秒数
        wait_seconds = (next_run - now).total_seconds()
        return wait_seconds
