"""AIM Client Scheduler — 消息调度器

Phase 0：三态状态机（IDLE/BUSY/OFFLINE）
从 Monitor（adapter.sh health）读 StateReport，决定是否投递消息。

关键原则：Scheduler 不自做判定，只消费 StateReport。
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


class Scheduler:
    """消息调度器

    状态机：
      OFFLINE → IDLE (health 恢复)
      IDLE → BUSY (开始投递)
      BUSY → IDLE (处理完成或超时)
      IDLE/BUSY → OFFLINE (health 连续 N 次 unhealthy)
    """

    def __init__(
        self,
        offline_threshold: int = 3,          # 连续 unhealthy → OFFLINE
        processing_timeout: float = 120.0,   # 单条处理超时
        health_probe_interval: float = 5.0,  # 探针间隔（秒）
        health_probe_max: float = 60.0,      # 探针最大间隔
        health_probe_backoff: float = 1.5,   # 探针退避系数
    ):
        self.offline_threshold = offline_threshold
        self.processing_timeout = processing_timeout
        self.health_probe_interval = health_probe_interval
        self.health_probe_max = health_probe_max
        self.health_probe_backoff = health_probe_backoff

        self._current_state = AgentState.IDLE
        self._last_state_report: Optional[StateReport] = None
        self._unhealthy_count = 0
        self._current_probe_interval = health_probe_interval
        self._processing_since: float = 0.0
        self._msg_count: int = 0

        # 回调
        self._on_dispatch: Optional[Callable[[], Awaitable[None]]] = None
        self._on_state_change: Optional[Callable[[AgentState, AgentState], Awaitable[None]]] = None

    # ── 属性 ──────────────────────────────────────────────

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

    # ── 输入：Monitor 报告 → Scheduler 决策 ───────────────

    def update_state(self, report: StateReport):
        """Monitor 更新状态报告。Scheduler 据此调整状态机。"""
        prev_state = self._current_state
        self._last_state_report = report

        if report.status == AgentState.OFFLINE:
            self._unhealthy_count += 1
            if self._unhealthy_count >= self.offline_threshold:
                if self._current_state != AgentState.OFFLINE:
                    self._transition(AgentState.OFFLINE)
                    self._current_probe_interval = min(
                        self._current_probe_interval * self.health_probe_backoff,
                        self.health_probe_max,
                    )
        else:
            self._unhealthy_count = 0
            self._current_probe_interval = self.health_probe_interval

            if self._current_state == AgentState.OFFLINE:
                # Runtime 恢复了 → IDLE
                self._transition(AgentState.IDLE)

            elif self._current_state == AgentState.BUSY and report.status == AgentState.IDLE:
                # Monitor 说 IDLE，但我们还在 BUSY → 检查超时
                if self._msg_count > 0 and time.time() - self._processing_since > self.processing_timeout:
                    logger.warning(f"处理超时 ({self.processing_timeout}s)，强制切 IDLE")
                    self._transition(AgentState.IDLE)

    def on_message_enqueued(self):
        """新消息入队通知。不立即切 BUSY，由 _try_dispatch 触发。"""
        # 只记录，不改变状态。dispatch 由外部调用驱动
        pass

    def on_dispatch_started(self):
        """开始投递 → 标记 BUSY"""
        if self._current_state == AgentState.IDLE:
            self._transition(AgentState.BUSY)
            self._processing_since = time.time()
            self._msg_count += 1

    def on_message_enqueued(self):
        """新消息入队通知。不立即切 BUSY，由 _try_dispatch 触发。"""
        pass
        """当前消息处理完成"""
        self._msg_count = max(0, self._msg_count - 1)
        self._transition(AgentState.IDLE)

    def on_timeout(self):
        """处理超时"""
        logger.warning("Scheduler: 处理超时，强制切 IDLE")
        self._transition(AgentState.IDLE)

    # ── 内部：状态转换 ────────────────────────────────────

    def _transition(self, new_state: AgentState):
        if new_state == self._current_state:
            return

        old = self._current_state
        self._current_state = new_state
        logger.info(f"🔄 Scheduler: {old.value} → {new_state.value}")

        if self._on_state_change:
            asyncio.ensure_future(self._on_state_change(old, new_state))

    # ── 决策：是否应该投递 ────────────────────────────────

    def should_dispatch(self) -> bool:
        """是否应该投递下一条消息"""
        if self._current_state == AgentState.OFFLINE:
            return False
        if self._current_state == AgentState.BUSY:
            return False
        # IDLE: 可以投递
        return True

    # ── 探针间隔 ──────────────────────────────────────────

    def get_probe_interval(self) -> float:
        return self._current_probe_interval

    # ── 诊断 ──────────────────────────────────────────────

    def status_summary(self) -> dict:
        return {
            "state": self._current_state.value,
            "unhealthy_count": self._unhealthy_count,
            "probe_interval": self._current_probe_interval,
            "msg_count": self._msg_count,
            "processing_elapsed": time.time() - self._processing_since if self._processing_since else 0,
            "last_report": self._last_state_report.status.value if self._last_state_report else "N/A",
        }
