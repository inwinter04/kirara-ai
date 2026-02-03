import asyncio
import random
import asyncio
from typing import Dict, List
from dataclasses import dataclass, field

from kirara_ai.logger import get_logger
from ..api_client import HuluxiaApiClient
from ..models import HuluxiaHeatSchedule

logger = get_logger("HeatTaskExecutor")


@dataclass
class CatExecutionResult:
    """单个板块的执行结果"""

    cat_id: int
    total: int
    success: int = 0
    failed: int = 0

    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.total == 0:
            return 0.0
        return (self.success / self.total) * 100


@dataclass
class HeatExecutionResult:
    """热度任务执行结果"""

    schedule_time: str
    cat_ids: List[int]
    cat_results: Dict[int, CatExecutionResult] = field(default_factory=dict)

    @property
    def total_success(self) -> int:
        """总成功次数"""
        return sum(r.success for r in self.cat_results.values())

    @property
    def total_failed(self) -> int:
        """总失败次数"""
        return sum(r.failed for r in self.cat_results.values())

    @property
    def total_requests(self) -> int:
        """总请求数"""
        return sum(r.total for r in self.cat_results.values())

    def add_cat_result(self, cat_id: int, result: CatExecutionResult):
        """添加板块执行结果"""
        self.cat_results[cat_id] = result


class HeatTaskExecutor:
    """
    热度任务执行器

    负责执行具体的热度请求，包括：
    - 发送HTTP请求增加板块热度
    - 控制请求延迟
    - 统计执行结果
    """

    def __init__(
        self,
        api_client: HuluxiaApiClient,
        delay_min_ms: int = 200,
        delay_max_ms: int = 300,
    ):
        """
        初始化执行器

        Args:
            api_client: API客户端实例
            delay_min_ms: 最小延迟（毫秒）
            delay_max_ms: 最大延迟（毫秒）
        """
        self.api_client = api_client
        self.delay_min_ms = delay_min_ms
        self.delay_max_ms = delay_max_ms

    async def execute_schedule(
        self, schedule: HuluxiaHeatSchedule
    ) -> HeatExecutionResult:
        """
        执行单个调度计划

        Args:
            schedule: 调度计划配置

        Returns:
            HeatExecutionResult: 执行结果统计
        """
        result = HeatExecutionResult(
            schedule_time=schedule.time, cat_ids=schedule.cat_ids
        )

        logger.info(
            f"[EXECUTOR] 开始执行调度计划: time={schedule.time}, "
            f"板块={schedule.cat_ids}, 每板块请求次数={schedule.request_count}"
        )

        for cat_id in schedule.cat_ids:
            logger.info(f"[EXECUTOR] 开始处理板块: cat_id={cat_id}")
            cat_result = await self._execute_for_cat_id(
                cat_id=cat_id, count=schedule.request_count
            )
            result.add_cat_result(cat_id, cat_result)
            logger.info(
                f"[EXECUTOR] 板块处理完成: cat_id={cat_id}, "
                f"成功={cat_result.success}, 失败={cat_result.failed}, "
                f"成功率={cat_result.success_rate:.1f}%"
            )

        return result

    async def _execute_for_cat_id(self, cat_id: int, count: int) -> CatExecutionResult:
        """
        对单个板块执行热度请求

        Args:
            cat_id: 板块ID
            count: 请求次数

        Returns:
            CatExecutionResult: 板块执行结果
        """
        success_count = 0
        failed_count = 0

        for i in range(count):
            try:
                # 发送热度请求
                await self.api_client.boost_board_heat(
                    cat_id=cat_id, device_code=self.api_client.device_code
                )

                success_count += 1

                # 在非最后一次请求后添加延迟
                if i < count - 1:
                    await self._delay()

            except Exception as e:
                failed_count += 1
                logger.error(
                    f"[EXECUTOR] 请求失败: cat_id={cat_id}, "
                    f"第{i + 1}/{count}次, error={e}"
                )

        return CatExecutionResult(
            cat_id=cat_id, total=count, success=success_count, failed=failed_count
        )

    async def _delay(self):
        """添加随机延迟"""
        delay_ms = random.randint(self.delay_min_ms, self.delay_max_ms)
        await asyncio.sleep(delay_ms / 1000)
