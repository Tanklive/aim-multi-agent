#!/usr/bin/env python3
"""
AIM RetryEventEmitter — 重传事件发射器（Observer 兼容版）

用途：
  Agent 重试机制产生的事件推送到 Observer 通道，
  用于监控面板、日志追踪、调试。

事件类型：
  - retry_start:    开始重试流程
  - retry_attempt:  第 N 次重试尝试
  - retry_success:  重试成功
  - retry_failed:   重试最终失败
  - message_expired: 消息过期（TTL 超时）
  - suspect_ttl:    目标 Agent 疑似不可达
  - backoff_triggered: 退避触发（防惊群）
  - recovered:      断连恢复

用法：
  emitter = RetryEventEmitter(broadcaster=observer_broadcast)
  emitter.add_local_handler(my_handler)
  await emitter.emit_retry_start(msg_id, "ZS0001", max_retries=3)
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ── 事件类型常量 ──────────────────────────

EVT_RETRY_START = "retry_start"
EVT_RETRY_ATTEMPT = "retry_attempt"
EVT_RETRY_SUCCESS = "retry_success"
EVT_RETRY_FAILED = "retry_failed"
EVT_MESSAGE_EXPIRED = "message_expired"
EVT_SUSPECT_TTL = "suspect_ttl"
EVT_BACKOFF_TRIGGERED = "backoff_triggered"
EVT_RECOVERED = "recovered"


# ── 事件数据 ──────────────────────────────


@dataclass
class RetryEvent:
    """重传事件"""
    event_type: str
    msg_id: str = ""
    target_agent_id: str = ""
    retry_count: int = 0
    max_retries: int = 0
    delay: float = 0.0
    offline_msg_count: int = 0
    detail: str = ""
    timestamp: float = field(default_factory=time.time)
    sequence_id: str = field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:8]}")


# ── 事件发射器 ─────────────────────────────


class RetryEventEmitter:
    """重传事件发射器 — 推送到 Observer + 本地处理器"""

    def __init__(self, broadcaster: Callable = None, hub_ref: object = None):
        """
        参数:
          broadcaster: 异步广播函数(payload: dict) — 推送到 Observer 通道
          hub_ref: AIMHub 引用（优先使用 hub_ref._broadcast_to_observers）
        """
        self.broadcaster = broadcaster
        self.hub_ref = hub_ref
        self._local_handlers: List[Callable] = []
        self._seq: int = 0

    def add_local_handler(self, handler: Callable):
        """添加本地事件处理器"""
        self._local_handlers.append(handler)

    def remove_local_handler(self, handler: Callable):
        """移除本地事件处理器"""
        if handler in self._local_handlers:
            self._local_handlers.remove(handler)

    def _next_seq(self) -> str:
        self._seq += 1
        return f"retry-{self._seq}"

    async def _dispatch(self, event: RetryEvent):
        """分发事件到本地处理器 + 广播"""
        # 本地处理器
        for handler in self._local_handlers:
            try:
                handler(event)
            except Exception:
                pass

        # 广播到 Observer
        payload = {
            "event_type": event.event_type,
            "msg_id": event.msg_id,
            "target_agent_id": event.target_agent_id,
            "retry_count": event.retry_count,
            "max_retries": event.max_retries,
            "delay": event.delay,
            "offline_msg_count": event.offline_msg_count,
            "detail": event.detail,
            "timestamp": event.timestamp,
            "sequence_id": event.sequence_id,
        }

        if self.hub_ref and hasattr(self.hub_ref, "_broadcast_to_observers"):
            try:
                await self.hub_ref._broadcast_to_observers(
                    event.target_agent_id,
                    {"cmd": "retry_event", "event": payload},
                )
            except Exception:
                pass
        elif self.broadcaster:
            try:
                await self.broadcaster(payload)
            except Exception:
                pass

    # ── 事件发射方法 ───────────────────────

    async def emit_retry_start(self, msg_id: str, target_agent_id: str, max_retries: int = 3):
        """发射: 开始重试流程"""
        event = RetryEvent(
            event_type=EVT_RETRY_START,
            msg_id=msg_id,
            target_agent_id=target_agent_id,
            max_retries=max_retries,
            detail=f"开始重试, 最多 {max_retries} 次",
        )
        await self._dispatch(event)

    async def emit_retry_attempt(
        self,
        msg_id: str,
        target_agent_id: str,
        retry_count: int,
        max_retries: int = 3,
    ):
        """发射: 第 N 次重试尝试"""
        event = RetryEvent(
            event_type=EVT_RETRY_ATTEMPT,
            msg_id=msg_id,
            target_agent_id=target_agent_id,
            retry_count=retry_count,
            max_retries=max_retries,
            detail=f"第 {retry_count}/{max_retries} 次重试",
        )
        await self._dispatch(event)

    async def emit_retry_success(self, msg_id: str, target_agent_id: str, retry_count: int = 0):
        """发射: 重试成功"""
        event = RetryEvent(
            event_type=EVT_RETRY_SUCCESS,
            msg_id=msg_id,
            target_agent_id=target_agent_id,
            retry_count=retry_count,
            detail=f"重试成功 (第 {retry_count} 次)",
        )
        await self._dispatch(event)

    async def emit_retry_failed(
        self,
        msg_id: str,
        target_agent_id: str,
        retry_count: int,
        max_retries: int = 3,
    ):
        """发射: 重试最终失败"""
        event = RetryEvent(
            event_type=EVT_RETRY_FAILED,
            msg_id=msg_id,
            target_agent_id=target_agent_id,
            retry_count=retry_count,
            max_retries=max_retries,
            detail=f"重试失败 ({retry_count}/{max_retries} 耗尽)",
        )
        await self._dispatch(event)

    async def emit_expired(self, msg_id: str, target_agent_id: str):
        """发射: 消息过期（TTL 超时）"""
        event = RetryEvent(
            event_type=EVT_MESSAGE_EXPIRED,
            msg_id=msg_id,
            target_agent_id=target_agent_id,
            detail="消息 TTL 超时, 放弃重试",
        )
        await self._dispatch(event)

    async def emit_backoff(self, msg_id: str, target_agent_id: str, delay: float):
        """发射: 退避触发（防惊群）"""
        event = RetryEvent(
            event_type=EVT_BACKOFF_TRIGGERED,
            msg_id=msg_id,
            target_agent_id=target_agent_id,
            delay=delay,
            detail=f"防惊群退避 {delay}s",
        )
        await self._dispatch(event)

    async def emit_recovered(self, agent_id: str, offline_msg_count: int = 0):
        """发射: 断连恢复"""
        event = RetryEvent(
            event_type=EVT_RECOVERED,
            target_agent_id=agent_id,
            offline_msg_count=offline_msg_count,
            detail=f"恢复在线, 离线消息 {offline_msg_count} 条",
        )
        await self._dispatch(event)
