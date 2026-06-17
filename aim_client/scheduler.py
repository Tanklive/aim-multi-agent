"""AIM Client Scheduler — 消息调度器

Phase 0：三态状态机（IDLE/BUSY/OFFLINE）
从 Monitor（adapter.sh health）读 StateReport，决定是否投递消息。

关键原则：Scheduler 不自做判定，只消费 StateReport。

状态机（对齐小火鸡儿 scheduler-state-rules.md v1）：
  OFFLINE - IDLE (health 恢复，exit=0)
  IDLE - BUSY (开始投递)
  BUSY - IDLE (处理完成或超时)
  IDLE/BUSY - OFFLINE (health exit=2，立即切换，不需 N 次确认)
"""
from __future__ import annotations
from enum import Enum, auto
from typing import Optional, Callable, Awaitable
import asyncio
import logging
import time

from .types import AgentState, StateReport

logger = logging.getLogger(__name__)


class SchedulerEvent(Enum):
    """Scheduler 事件"""
    STATE_CHANGED = auto()      # Monitor 报告状态变更
    MESSAGE_ENQUEUED = auto()   # 新消息入队
    PROCESSING_DONE = auto()    # 当前消息处理完成
    TIMEOUT = auto()             # 处理超时


# 探针间隔固定数组（对齐小火鸡儿规则文档）
# 0-3: 正常递增，4+: 稳定到 60s（上限）
_PROBE_INTERVALS = [5, 10, 15, 30, 60]


class Scheduler:
    """消息调度器

    状态机：
      OFFLINE - IDLE (health 恢复，exit=0)
      IDLE - BUSY (开始投递)
      BUSY - IDLE (处理完成或超时)
      IDLE/BUSY - OFFLINE (health exit=2，立即切换)

    探针间隔使用固定数组 [5, 10, 15, 30, 60] 秒，
    对齐小火鸡儿 scheduler-state-rules.md。
    """

    def __init__(
        self,
        processing_timeout: float = 120.0,   # 单条处理超时
        health_probe_interval: float = 5.0,  # 探针间隔（秒）
    ):
        self.processing_timeout = processing_timeout
        self.health_probe_interval = health_probe_interval

        self._current_state = AgentState.IDLE
        self._last_state_report: Optional[StateReport] = None
        self._offline_count: int = 0  # 连续 offline 次数（用于探针退避）
        self._processing_since: float = 0.0
        self._msg_count: int = 0

        # 回调
        self._on_dispatch: Optional[Callable[[], Awaitable[None]]] = None
        self._on_state_change: Optional[Callable[[AgentState, AgentState], Awaitable[None]]] = None

    # -- 属性 --

    @property
    def state(self) -> AgentState:
        return self._current_state

    @property
    def is_idle(self) -> bool:
        return self._current_state == AgentState.IDLE

    @property
    def is_busy(self) -> bool:
        return self._current_state == AgentState.BUSY

    @property
    def is_offline(self) -> bool:
        return self._current_state == AgentState.OFFLINE

    # -- 输入：Monitor 报告 - Scheduler 决策 --

    def update_state(self, report: StateReport):
        """Monitor 更新状态报告。Scheduler 据此调整状态机。

        判定规则 (v1.2，对齐小火鸡儿 scheduler-state-rules.md)：
        - exit 2 (OFFLINE): Runtime 挂了 - 立即切 OFFLINE，退避探针
        - exit 1 (BUSY): 框架忙 - 维持当前状态，不计数
        - exit 0 (IDLE): 健康 - 重置探针间隔；若之前 OFFLINE 则恢复
        """
        prev_state = self._current_state
        self._last_state_report = report

        if report.status == AgentState.OFFLINE:
            # exit 2 = unhealthy，Runtime 进程挂了 - 立即 OFFLINE
            if self._current_state != AgentState.OFFLINE:
                self._transition(AgentState.OFFLINE)
                self._offline_count = 0  # 首次 OFFLINE，探针走第 0 档 (5s)
            else:
                # 持续 OFFLINE - 递增计数用于探针退避
                self._offline_count += 1

        elif report.status == AgentState.BUSY:
            # exit 1 = degraded，框架忙 - 维持当前状态，不切换
            pass

        else:
            # exit 0 = healthy - 重置
            self._offline_count = 0

            if self._current_state == AgentState.OFFLINE:
                # Runtime 恢复了 - IDLE
                self._transition(AgentState.IDLE)

            elif self._current_state == AgentState.BUSY and report.status == AgentState.IDLE:
                # Monitor 说 IDLE，但我们还在 BUSY - 检查超时
                if self._msg_count > 0 and time.time() - self._processing_since > self.processing_timeout:
                    self._transition(AgentState.IDLE)

    def on_message_enqueued(self):
        """新消息入队通知。不立即切 BUSY，由 _try_dispatch 触发。"""
        pass

    def on_dispatch_started(self):
        """开始投递 - 标记 BUSY"""
        if self._current_state == AgentState.IDLE:
            self._transition(AgentState.BUSY)
            self._processing_since = time.time()
            self._msg_count += 1

    def on_processing_done(self):
        """当前消息处理完成（exit=0，已发送回复）"""
        self._msg_count = max(0, self._msg_count - 1)
        self._transition(AgentState.IDLE)

    def on_retry(self):
        """可重试（exit=1）：session 忙等。状态不变，等待下一轮探针。"""
        # exit=1 不切换状态，探针按 degraded 处理
        logger.info("Scheduler: adapter exit=1，可重试，状态不变")

    def on_degrade(self):
        """降级（exit=2）：Runtime 不可用，切 OFFLINE。"""
        logger.warning("Scheduler: adapter exit=2，降级")
        self._transition(AgentState.OFFLINE)

    def on_human_intervention(self):
        """需人工介入（exit=3）：框架崩溃等。切 OFFLINE 等大哥处理。"""
        logger.error("Scheduler: adapter exit=3，需人工介入")
        self._transition(AgentState.OFFLINE)

    def on_timeout(self):
        """处理超时"""
        logger.warning("Scheduler: 处理超时，强制切 IDLE")
        self._transition(AgentState.IDLE)

    # -- 内部：状态转换 --

    def _transition(self, new_state: AgentState):
        if new_state == self._current_state:
            return

        old = self._current_state
        self._current_state = new_state
        logger.info(f" Scheduler: {old.value} - {new_state.value}")

        if self._on_state_change:
            asyncio.ensure_future(self._on_state_change(old, new_state))

    # -- 决策：是否应该投递 --

    def should_dispatch(self) -> bool:
        """是否应该投递下一条消息"""
        if self._current_state == AgentState.OFFLINE:
            return False
        if self._current_state == AgentState.BUSY:
            return False
        # IDLE: 可以投递
        return True

    # -- 探针间隔 --

    def get_probe_interval(self) -> float:
        """返回当前探针间隔（秒）

        对齐小火鸡儿规则文档固定数组 [5, 10, 15, 30, 60]：
        - IDLE/正常: 固定 5s
        - OFFLINE: 按连续 offline 次数从数组中取值，上限 60s
        """
        if self._current_state == AgentState.OFFLINE:
            idx = min(self._offline_count, len(_PROBE_INTERVALS) - 1)
            return float(_PROBE_INTERVALS[idx])
        return float(self.health_probe_interval)

    # -- 诊断 --

    def status_summary(self) -> dict:
        return {
            "state": self._current_state.value,
            "probe_interval": self.get_probe_interval(),
            "offline_count": self._offline_count,
            "msg_count": self._msg_count,
            "processing_elapsed": time.time() - self._processing_since if self._processing_since else 0,
            "last_report": self._last_state_report.status.value if self._last_state_report else "N/A",
        }
