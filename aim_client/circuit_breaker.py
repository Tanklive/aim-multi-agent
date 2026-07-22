"""AIM Client — dispatch 层熔断截流器

三态断路器：CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN

设计目标（2026-07-18 v1.6）：
  - dispatch 层截流：在 dequeue 前拦截，不消耗队列
  - 与现有 L1/L2 全局熔断互补：L1/L2 管全局降级，断路器管单 adapter 外部依赖超时
  - 适配所有 agent：不依赖 adapter.sh 返回值，只统计 dispatch loop 中的超时/成功计数

规则：
  CLOSED:    正常投递。连续 N 次 timeout → OPEN
  OPEN:      截流不投递。cooldown 秒后 → HALF_OPEN
  HALF_OPEN: 放行一条试探。成功 → CLOSED；失败 → OPEN（重置 cooldown）

兼容性：
  - 不改 adapter.sh、不改 scheduler 状态机、不改 _call_adapter 返回值语义
  - 只拦截 dispatch_loop 中 should_dispatch 到 dequeue 之间
"""

from __future__ import annotations
from enum import Enum
import time


class BreakerState(Enum):
    CLOSED = "closed"          # 正常 · 消息照投
    OPEN = "open"              # 熔断 · 截流不投
    HALF_OPEN = "half_open"    # 试探 · 放一条


class DispatchBreaker:
    """单 adapter 的 dispatch 级熔断器。

    不共享状态——各 agent 各自实例化（各自统计自家 adapter timeout）。
    """

    def __init__(
        self,
        failure_threshold: int = 5,      # 连续 timeout N 次 → OPEN
        cooldown_sec: float = 60.0,      # OPEN 后等多久 → HALF_OPEN
        half_open_probe_sec: float = 30.0,  # HALF_OPEN 如果无消息发，等多久自动放试探
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self.half_open_probe_sec = half_open_probe_sec

        self._state = BreakerState.CLOSED
        self._consecutive_timeouts: int = 0
        self._last_timeout_at: float = 0.0
        self._opened_at: float = 0.0
        self._total_transitions: int = 0  # 诊断用

    # ── 属性 ──

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def is_open(self) -> bool:
        """dispatch_loop 用：截流检查点"""
        self._auto_transition()
        return self._state == BreakerState.OPEN

    @property
    def consecutive_timeouts(self) -> int:
        return self._consecutive_timeouts

    # ── 事件输入（dispatch_loop 在每次 adapter 调用后调用） ──

    def on_timeout(self):
        """adapter 调用超时（asyncio.TimeoutError）"""
        self._consecutive_timeouts += 1
        self._last_timeout_at = time.time()

        # 从 HALF_OPEN 炸回 OPEN：立刻
        if self._state == BreakerState.HALF_OPEN:
            self._set_state(BreakerState.OPEN, "HALF_OPEN 试探超时 → OPEN")
            return

        if self._state == BreakerState.CLOSED and self._consecutive_timeouts >= self.failure_threshold:
            self._set_state(BreakerState.OPEN, f"连续 {self._consecutive_timeouts} 次 timeout → OPEN")
            self._opened_at = time.time()

    def on_success(self):
        """adapter 调用成功（正常返回，不论是否有内容）"""
        self._consecutive_timeouts = 0

        if self._state == BreakerState.HALF_OPEN:
            self._set_state(BreakerState.CLOSED, "HALF_OPEN 试探成功 → CLOSED")

    def on_empty_response(self):
        """adapter 返回空内容（不算 timeout，但也不算"真正成功"）。
        如果连续空响应太多，可能是适配器半死不活——暂时只重置 timeout 计数，不改变状态。
        """
        self._consecutive_timeouts = 0

    # ── 强制操作 (健康探针恢复、运维手动重置) ──

    def force_close(self, reason: str = "manual"):
        """强制回到 CLOSED（健康探针确认恢复时）"""
        self._consecutive_timeouts = 0
        self._set_state(BreakerState.CLOSED, f"force_close: {reason}")

    def force_open(self, reason: str = "manual"):
        """手动熔断"""
        self._consecutive_timeouts = self.failure_threshold  # 确保判定
        self._set_state(BreakerState.OPEN, f"force_open: {reason}")
        self._opened_at = time.time()

    # ── 内部 ──

    def _auto_transition(self):
        """检查是否应该从 OPEN → HALF_OPEN（cooldown 到期自动转）"""
        if self._state == BreakerState.OPEN:
            elapsed = time.time() - self._opened_at
            if elapsed >= self.cooldown_sec:
                self._set_state(BreakerState.HALF_OPEN, f"cooldown {self.cooldown_sec:.0f}s 到期 → HALF_OPEN")

    def _set_state(self, new: BreakerState, reason: str):
        if new == self._state:
            return
        old = self._state
        self._state = new
        self._total_transitions += 1
        # 日志：通过调用方 logger 输出，这里只记录
        self._last_transition_reason = reason
        self._last_transition_at = time.time()

    # ── 诊断 ──

    def status_summary(self) -> dict:
        self._auto_transition()  # 确保返回最新
        return {
            "state": self._state.value,
            "consecutive_timeouts": self._consecutive_timeouts,
            "total_transitions": self._total_transitions,
            "failure_threshold": self.failure_threshold,
            "cooldown_sec": self.cooldown_sec,
            "opened_at": self._opened_at if self._opened_at else None,
            "last_timeout_at": self._last_timeout_at if self._last_timeout_at else None,
        }
