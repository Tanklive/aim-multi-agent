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

from .types import AgentState, StateReport, DegradeLevel, evaluate_degrade_level, get_probe_interval as _get_probe_interval

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
        # 降级阈值（Phase 1）
        l1_trigger_timeouts: int = 3,        # 连续超时触发 L1 的阈值
        l2_trigger_health_fails: int = 3,    # 连续 health 失败触发 L2 的阈值
    ):
        self.processing_timeout = processing_timeout
        self.health_probe_interval = health_probe_interval
        self.l1_trigger_timeouts = l1_trigger_timeouts
        self.l2_trigger_health_fails = l2_trigger_health_fails

        self._current_state = AgentState.IDLE
        self._degrade_level = DegradeLevel.L0  # Phase 1：当前降级级别
        self._last_state_report: Optional[StateReport] = None
        self._offline_count: int = 0  # 连续 offline 次数（用于探针退避）
        self._retry_count: int = 0    # 连续 exit=1 次数（Phase 1：L1 判定用）
        self._processing_since: float = 0.0
        self._msg_count: int = 0

        # 回调
        self._on_dispatch: Optional[Callable[[], Awaitable[None]]] = None
        self._on_state_change: Optional[Callable[[AgentState, AgentState], Awaitable[None]]] = None
        self._on_degrade_change: Optional[Callable[[DegradeLevel, DegradeLevel, str], Awaitable[None]]] = None  # Phase 1

    # -- 属性 --

    @property
    def state(self) -> AgentState:
        return self._current_state

    @property
    def degrade_level(self) -> DegradeLevel:
        """当前降级级别（Phase 1）"""
        return self._degrade_level

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
        """Monitor 更新状态报告。Scheduler 据此调整状态机和降级级别。

        Phase 1 (v1.3): 集成 evaluate_degrade_level()，health 恢复时自动 L1/L2→L0。
        """
        prev_state = self._current_state
        self._last_state_report = report

        # ── 降级级别判定（Phase 1：health 探针带回的信息也走 evaluate）──
        health_exit = 0
        if report.status == AgentState.OFFLINE:
            health_exit = 2
        elif report.status == AgentState.BUSY:
            health_exit = 1

        # OFFILNE 计数管理
        if report.status == AgentState.OFFLINE:
            if self._current_state != AgentState.OFFLINE:
                self._transition(AgentState.OFFLINE)
                self._offline_count = 0
            else:
                self._offline_count += 1
        elif report.status == AgentState.BUSY:
            pass  # 维持当前状态
        else:
            self._offline_count = 0
            if self._current_state == AgentState.OFFLINE:
                self._transition(AgentState.IDLE)

        # ── Phase 1: health 恢复时评估降级恢复 ──
        # health exit=0 时，如果之前是 L1/L2，评估能否恢复到 L0
        if health_exit == 0 and self._degrade_level != DegradeLevel.L0:
            new_level, reason = evaluate_degrade_level(
                health_exit_code=0,
                consecutive_timeouts=self._retry_count,
                consecutive_health_fails=0,
                current_level=self._degrade_level,
                l1_trigger_timeouts=self.l1_trigger_timeouts,
                l2_trigger_health_fails=self.l2_trigger_health_fails,
            )
            if new_level != self._degrade_level:
                self._set_degrade_level(new_level, reason)
        elif health_exit == 2:
            # health 失败 → 评估是否触发 L2
            new_level, reason = evaluate_degrade_level(
                health_exit_code=2,
                consecutive_timeouts=self._retry_count,
                consecutive_health_fails=self._offline_count,
                current_level=self._degrade_level,
                l1_trigger_timeouts=self.l1_trigger_timeouts,
                l2_trigger_health_fails=self.l2_trigger_health_fails,
            )
            if new_level != self._degrade_level:
                self._set_degrade_level(new_level, reason)

    def on_message_enqueued(self):
        """新消息入队通知。不立即切 BUSY，由 _try_dispatch 触发。"""
        pass

    def on_dispatch_started(self):
        """开始投递 - 标记 BUSY"""
        if self._current_state == AgentState.IDLE:
            self._transition(AgentState.BUSY)
            self._processing_since = time.time()
            self._msg_count += 1
            # Phase 1: 成功 dispatch 清零 retry 计数
            self._retry_count = 0

    def on_processing_done(self):
        """当前消息处理完成（exit=0，已发送回复）"""
        self._msg_count = max(0, self._msg_count - 1)
        self._transition(AgentState.IDLE)

    def on_retry(self):
        """可重试（exit=1）：session 忙等。

        Phase 1: 连续超时计数 → evaluate_degrade_level() 判定 L1。
        """
        self._retry_count += 1
        logger.info("Scheduler: adapter exit=1，可重试 (连续第 %d 次)", self._retry_count)

        new_level, reason = evaluate_degrade_level(
            health_exit_code=0,  # health 探针不受 exit=1 影响
            consecutive_timeouts=self._retry_count,
            consecutive_health_fails=0,
            current_level=self._degrade_level,
            l1_trigger_timeouts=self.l1_trigger_timeouts,
            l2_trigger_health_fails=self.l2_trigger_health_fails,
        )
        if new_level != self._degrade_level:
            self._set_degrade_level(new_level, reason)
        # exit=1 不切换三态状态机，维持当前 state

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

    def _set_degrade_level(self, new_level: DegradeLevel, reason: str):
        """Phase 1: 降级级别变更，触发回调"""
        if new_level == self._degrade_level:
            return

        old = self._degrade_level
        self._degrade_level = new_level
        logger.info(" Scheduler: DegradeLevel %s → %s (%s)", old.value, new_level.value, reason)

        if self._on_degrade_change:
            asyncio.ensure_future(self._on_degrade_change(old, new_level, reason))

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

        Phase 1: L1 降级时使用 DegradeLevel 对应的探针递增策略。
        L0: 固定 5s
        L1: 5s→10s→15s（按连续 retry 次数）
        L2/OFFLINE: 按连续 offline 次数从数组 [5,10,15,30,60] 取值
        """
        if self._degrade_level == DegradeLevel.L1:
            return float(_get_probe_interval(DegradeLevel.L1, self._retry_count))
        if self._current_state == AgentState.OFFLINE or self._degrade_level == DegradeLevel.L2:
            idx = min(self._offline_count, len(_PROBE_INTERVALS) - 1)
            return float(_PROBE_INTERVALS[idx])
        return float(self.health_probe_interval)

    # -- 诊断 --

    def status_summary(self) -> dict:
        return {
            "state": self._current_state.value,
            "degrade_level": self._degrade_level.value,  # Phase 1
            "probe_interval": self.get_probe_interval(),
            "offline_count": self._offline_count,
            "retry_count": self._retry_count,  # Phase 1
            "msg_count": self._msg_count,
            "processing_elapsed": time.time() - self._processing_since if self._processing_since else 0,
            "last_report": self._last_state_report.status.value if self._last_state_report else "N/A",
        }
