"""AIM Client Queue — 消息缓存队列

Phase 1：内存队列 + JSONL 持久化
  - enqueue/ack/nack 异步写入 JSONL
  - 启动时从文件恢复未 ack 的消息
  - 文件 > 50KB 自动压缩
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Callable
import time
import asyncio
import logging

from .types import Message
from .queue_persist import QueuePersist

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

    持久化：
      JSONL 追加写入 ~/shared/aim/data/queue.jsonl
      enqueue/ack/nack 异步记录，启动时恢复
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
        self._persist: Optional[QueuePersist] = None

    # ── 核心接口 ──────────────────────────────────────────

    def enqueue(self, msg: Message) -> str:
        """入队。容量满了丢弃最旧消息。"""
        if len(self._pending) >= self.capacity:
            dropped = self._pending.popleft()
            logger.warning(f"队列满({self.capacity})，丢弃最旧消息: {dropped.msg_id}")
            self._stats.total_failed += 1

        self._pending.append(msg)
        self._stats.pending = len(self._pending)

        # 异步持久化
        self._schedule_persist(lambda: self._persist.write_enqueue(msg))

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
        msg.dequeued_at = time.time()
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

            # 异步持久化
            self._schedule_persist(lambda: self._persist.write_ack(msg_id))

            logger.debug(f"✅ ack: {msg_id}")
        else:
            logger.warning(f"ack 未知消息: {msg_id}（当前 processing={getattr(self._processing, 'msg_id', None)}）")

    def nack(self, msg_id: str, reason: str = ""):
        """处理失败，放回队头（重试）或丢入 dead 队列"""
        if self._processing and self._processing.msg_id == msg_id:
            msg = self._processing
            is_dead = False
            if msg.dequeued_at > 0 and (time.time() - msg.dequeued_at) > self.processing_timeout:
                # 超时 → dead 队列
                self._dead.append((msg, time.time() + self.dead_ttl))
                self._stats.total_failed += 1
                is_dead = True
                logger.warning(f"💀 超时进 dead: {msg_id} reason={reason}")
            else:
                # 未超时 → 放回队头重试
                self._pending.appendleft(msg)

            self._processing = None
            self._stats.processing = 0
            self._stats.pending = len(self._pending)

            # 持久化：仅 dead 记录 nack，retry 消息仍处于 pending 状态
            if is_dead and self._persist:
                self._schedule_persist(lambda: self._persist.write_nack(msg_id, reason))
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

    # ── 持久化接口 ────────────────────────────────────────

    async def init_persist(self, filepath: str = ""):
        """初始化持久化层并恢复消息"""
        from pathlib import Path
        path = Path(filepath) if filepath else None
        self._persist = QueuePersist(filepath=path)
        await self._persist.start()

        # 从文件恢复未 ack 的消息
        restored = await self._persist.restore()
        if restored:
            for msg in restored:
                self._pending.append(msg)
            self._stats.pending = len(self._pending)
            logger.info(f"📦 持久化恢复 {len(restored)} 条消息到 pending 队列")

    async def close_persist(self):
        """关闭持久化层"""
        if self._persist:
            await self._persist.stop()
            self._persist = None

    @property
    def has_persist(self) -> bool:
        return self._persist is not None

    def _schedule_persist(self, fn: Callable):
        """在当前事件循环中调度持久化写入"""
        if self._persist is None:
            return
        try:
            asyncio.create_task(fn())
        except RuntimeError:
            pass  # 不在事件循环中，跳过
