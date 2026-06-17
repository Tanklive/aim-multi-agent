"""
AIM 消息重传管理器
SQLite 持久化 pending 队列 + 指数退避 + 去重
"""

import asyncio
import json
import sqlite3
import time
import logging
from pathlib import Path
from typing import Optional, Callable, Dict, Set
from dataclasses import dataclass

log = logging.getLogger("aim-retry")


@dataclass
class RetryPolicy:
    """重传策略"""
    initial_delay: float = 1.0      # 初始延迟 (秒)
    multiplier: float = 2.0         # 倍数
    max_delay: float = 30.0         # 最大延迟 (秒)
    max_retries: int = 5            # 最大重传次数
    ack_timeout: float = 30.0       # ACK 超时 (秒)


class RetryManager:
    """消息重传管理器"""

    def __init__(self, agent_id: str, db_path: str = None, policy: RetryPolicy = None):
        self.agent_id = agent_id
        self.db_path = db_path or f"/tmp/aim_retry_{agent_id}.db"
        self.policy = policy or RetryPolicy()
        self._seen_sequences: Set[int] = set()
        self._emit_callback: Optional[Callable] = None
        self._send_callback: Optional[Callable] = None
        self._running = False

        self._init_db()

    def _init_db(self):
        """初始化 SQLite 数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_messages (
                msg_id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                content TEXT NOT NULL,
                msg_type TEXT DEFAULT 'text',
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                next_retry_at REAL,
                created_at REAL,
                last_error TEXT,
                sequence INTEGER
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON pending_messages(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_next_retry ON pending_messages(next_retry_at)
        """)
        conn.commit()
        conn.close()
        log.info(f"✅ RetryManager 数据库初始化: {self.db_path}")

    def on_emit(self, callback: Callable):
        """设置事件回调（Observer 推送）"""
        self._emit_callback = callback

    def on_send(self, callback: Callable):
        """设置发送回调"""
        self._send_callback = callback

    # ── 发送方：消息入队 ──────────────────────────────

    async def send_with_retry(self, to_id: str, content: str, msg_type: str = "text", sequence: int = 0) -> str:
        """发送消息并加入重传队列"""
        import uuid
        msg_id = str(uuid.uuid4())[:12]
        now = time.time()

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO pending_messages 
            (msg_id, from_id, to_id, content, msg_type, status, retry_count, next_retry_at, created_at, sequence)
            VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
        """, (msg_id, self.agent_id, to_id, content, msg_type, now + self.policy.ack_timeout, now, sequence))
        conn.commit()
        conn.close()

        # 发送消息
        if self._send_callback:
            await self._send_callback(to_id, content, msg_type, msg_id)

        log.info(f"📤 消息入队: {msg_id} → {to_id}")
        await self._emit_retry_event("send", msg_id, 0, "消息已发送，等待ACK")

        return msg_id

    # ── 接收方：确认消息 ──────────────────────────────

    async def ack_message(self, msg_id: str):
        """确认消息（收到 ACK）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "UPDATE pending_messages SET status = 'confirmed' WHERE msg_id = ?", (msg_id,)
        )
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            log.info(f"✅ 消息确认: {msg_id}")
            await self._emit_retry_event("ack", msg_id, 0, "ACK 已收到")

    # ── 接收方：去重检查 ──────────────────────────────

    def is_duplicate(self, sequence: int) -> bool:
        """检查消息是否重复"""
        if sequence in self._seen_sequences:
            return True
        self._seen_sequences.add(sequence)

        # 清理过期序列号（保留最近 1000 条）
        if len(self._seen_sequences) > 1000:
            self._seen_sequences = set(sorted(self._seen_sequences)[-1000:])

        return False

    # ── 重传循环 ──────────────────────────────────────

    async def start_retry_loop(self):
        """启动重传循环"""
        self._running = True
        log.info("🔄 重传循环启动")

        while self._running:
            try:
                await self._check_and_retry()
                await asyncio.sleep(1)  # 每秒检查一次
            except Exception as e:
                log.error(f"重传循环异常: {e}")
                await asyncio.sleep(5)

    async def stop_retry_loop(self):
        """停止重传循环"""
        self._running = False
        log.info("🛑 重传循环停止")

    async def _check_and_retry(self):
        """检查并重传超时消息"""
        now = time.time()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT msg_id, to_id, content, msg_type, retry_count, sequence
            FROM pending_messages
            WHERE status = 'pending' AND next_retry_at <= ?
        """, (now,))

        rows = cursor.fetchall()
        conn.close()

        for msg_id, to_id, content, msg_type, retry_count, sequence in rows:
            if retry_count >= self.policy.max_retries:
                await self._mark_failed(msg_id, "超过最大重传次数")
                continue

            # 计算下次重传时间
            delay = min(
                self.policy.initial_delay * (self.policy.multiplier ** retry_count),
                self.policy.max_delay
            )
            next_retry = now + delay

            # 更新重传计数
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                UPDATE pending_messages 
                SET retry_count = retry_count + 1, next_retry_at = ?, status = 'retrying'
                WHERE msg_id = ?
            """, (next_retry, msg_id))
            conn.commit()
            conn.close()

            # 重传
            if self._send_callback:
                await self._send_callback(to_id, content, msg_type, msg_id)
                log.info(f"🔄 重传消息: {msg_id} (第{retry_count + 1}次)")
                await self._emit_retry_event(
                    "retry", msg_id, retry_count + 1,
                    f"重传第{retry_count + 1}次，下次重传 {delay:.1f}s 后"
                )

    async def _mark_failed(self, msg_id: str, reason: str):
        """标记消息为失败"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            UPDATE pending_messages SET status = 'failed', last_error = ? WHERE msg_id = ?
        """, (reason, msg_id))
        conn.commit()
        conn.close()

        log.warning(f"❌ 消息失败: {msg_id} | {reason}")
        await self._emit_retry_event("failed", msg_id, 0, reason)

    # ── 事件推送 ──────────────────────────────────────

    async def _emit_retry_event(self, event_type: str, msg_id: str, retry_count: int, reason: str):
        """推送重传事件到 Observer"""
        if self._emit_callback:
            event = {
                "type": f"retry_{event_type}",
                "agent_id": self.agent_id,
                "msg_id": msg_id,
                "retry_count": retry_count,
                "reason": reason,
                "ts": time.time(),
            }
            await self._emit_callback(event)

    # ── 统计 ──────────────────────────────────────────

    def stats(self) -> dict:
        """获取统计信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT status, COUNT(*) FROM pending_messages GROUP BY status
        """)
        stats = dict(cursor.fetchall())
        conn.close()

        return {
            "total": sum(stats.values()),
            "pending": stats.get("pending", 0),
            "confirmed": stats.get("confirmed", 0),
            "retrying": stats.get("retrying", 0),
            "failed": stats.get("failed", 0),
            "seen_sequences": len(self._seen_sequences),
        }

    # ── 恢复 ──────────────────────────────────────────

    async def recover_pending(self):
        """恢复待处理消息（Agent 重启后）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT msg_id, to_id, content, msg_type, retry_count
            FROM pending_messages
            WHERE status IN ('pending', 'retrying')
        """)
        rows = cursor.fetchall()
        conn.close()

        if rows:
            log.info(f"🔄 恢复 {len(rows)} 条待处理消息")
            for msg_id, to_id, content, msg_type, retry_count in rows:
                if self._send_callback:
                    await self._send_callback(to_id, content, msg_type, msg_id)
                    log.info(f"📤 恢复发送: {msg_id} → {to_id}")

    # ── 清理 ──────────────────────────────────────────

    def cleanup(self, max_age_days: int = 7):
        """清理过期消息"""
        cutoff = time.time() - (max_age_days * 86400)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "DELETE FROM pending_messages WHERE created_at < ? AND status = 'confirmed'",
            (cutoff,)
        )
        conn.commit()
        conn.close()
        log.info(f"🧹 清理 {cursor.rowcount} 条过期消息")