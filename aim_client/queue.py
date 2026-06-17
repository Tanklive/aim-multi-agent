"""AIM Client Queue — 消息缓存队列

Phase 0：内存队列 + 可选的 JetStream 双写
Phase 1：JetStream KV 持久化
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Callable
import time
import json
import logging

from .types import Message

logger = logging.getLogger(__name__)


@dataclass
class QueueStats:
    pending: int = 0
    processing: int = 0
    dead: int = 0
    total_processed: int = 0
    total_failed: int = 0


class MessageQueue:
    """消息缓存队列

    结构：
      pending: deque[Message]      # 等待投递
      processing: Optional[Message] # 正在处理（同时只一个）
      dead: deque[Message]         # 超时/失败（TTL 24h）
    """

    def __init__(
        self,
        capacity: int = 1000,
        processing_timeout: float = 120.0,
        dead_ttl: float = 86400.0,  # 24h
        persist_path: Optional[str] = None,
    ):
        self.capacity = capacity
        self.processing_timeout = processing_timeout
        self.dead_ttl = dead_ttl
        self.persist_path = persist_path

        self._pending: deque[Message] = deque()
        self._processing: Optional[Message] = None
        self._dead: deque[tuple[Message, float]] = deque()  # (msg, expired_at)
        self._stats = QueueStats()
        self._on_dequeue: Optional[Callable] = None  # Scheduler 回调

    # ── 核心接口 ──────────────────────────────────────────

    def enqueue(self, msg: Message) -> str:
        """入队。容量满了丢弃最旧消息。"""
        if len(self._pending) >= self.capacity:
            dropped = self._pending.popleft()
            logger.warning(f"队列满({self.capacity})，丢弃最旧消息: {dropped.msg_id}")
            self._stats.total_failed += 1

        self._pending.append(msg)
        self._stats.pending = len(self._pending)
        logger.debug(f"📥 enqueue: {msg.msg_id} (pending={self._stats.pending})")
        return msg.msg_id

    def dequeue(self) -> Optional[Message]:
        """出队并标记为 processing。同时只处理一条。"""
        if self._processing is not None:
            logger.debug(f"已有消息在处理中: {self._processing.msg_id}")
            return None
        if not self._pending:
            return None

        msg = self._pending.popleft()
        self._processing = msg
        self._stats.processing = 1
        self._stats.pending = len(self._pending)
        logger.debug(f"📤 dequeue: {msg.msg_id} → processing")
        return msg

    def ack(self, msg_id: str):
        """确认处理成功"""
        if self._processing and self._processing.msg_id == msg_id:
            self._stats.total_processed += 1
            self._processing = None
            self._stats.processing = 0
            logger.debug(f"✅ ack: {msg_id}")
        else:
            logger.warning(f"ack 未知消息: {msg_id}（当前 processing={getattr(self._processing, 'msg_id', None)}）")

    def nack(self, msg_id: str, reason: str = ""):
        """处理失败，放回队头（重试）或丢入 dead 队列"""
        if self._processing and self._processing.msg_id == msg_id:
            msg = self._processing
            if msg.received_at and (time.time() - msg.received_at) > self.processing_timeout:
                # 超时 → dead 队列
                self._dead.append((msg, time.time() + self.dead_ttl))
                self._stats.total_failed += 1
                logger.warning(f"💀 超时进 dead: {msg_id} reason={reason}")
            else:
                # 未超时 → 放回队头重试
                self._pending.appendleft(msg)

            self._processing = None
            self._stats.processing = 0
            self._stats.pending = len(self._pending)
        else:
            logger.warning(f"nack 未知消息: {msg_id}")

    # ── 查询接口 ──────────────────────────────────────────

    def peek(self, limit: int = 10) -> List[Message]:
        return list(self._pending)[:limit]

    def size(self) -> int:
        return len(self._pending)

    def is_idle(self) -> bool:
        return self._processing is None

    def stats(self) -> QueueStats:
        self._stats.pending = len(self._pending)
        self._stats.processing = 1 if self._processing else 0
        self._stats.dead = len(self._dead)
        return self._stats

    # ── 维护 ──────────────────────────────────────────────

    def purge_dead(self):
        """清理过期的 dead 消息"""
        now = time.time()
        kept = [(m, exp) for m, exp in self._dead if exp > now]
        removed = len(self._dead) - len(kept)
        self._dead = deque(kept)
        if removed:
            logger.info(f"🧹 清理 dead 队列: {removed} 条")

    def set_on_dequeue(self, callback: Callable):
        """设置 dequeue 时的回调（供 Scheduler 使用）"""
        self._on_dequeue = callback
