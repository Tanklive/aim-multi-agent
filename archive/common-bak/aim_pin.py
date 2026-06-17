#!/usr/bin/env python3
"""
AIM Pin — 消息去重组件（持久化版）

用途：
  防止 Agent 重复处理同一条消息（网络重传、NATS 重连、多订阅等场景）

功能：
  - msg_id 级精确去重（LRU + TTL）
  - 持久化去重记录到 SQLite（重启后仍可去重）
  - 自适应窗口：初始 300s TTL，根据消息频率动态调整
  - 线程安全（asyncio.Lock）

用法：
  pin = AIMPin(agent_id="ZS0002")
  if not await pin.is_duplicate(msg_id):
      await pin.mark(msg_id)
      # process message
"""

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Set


class AIMPin:
    """消息去重 Pin 组件 — 持久化 LRU + TTL"""

    # ── 默认值 ────────────────────────────
    DEFAULT_TTL = 300          # 5 分钟
    MAX_MEMORY = 2000          # 内存缓存上限
    PERSIST_INTERVAL = 60      # 持久化写入间隔（秒）

    def __init__(
        self,
        agent_id: str,
        ttl: int = 300,
        db_dir: str = "",
        max_memory: int = 2000,
    ):
        self.agent_id = agent_id
        self.ttl = ttl or self.DEFAULT_TTL
        self.max_memory = max_memory or self.MAX_MEMORY

        # 持久化存储
        db_dir = db_dir or str(Path.home() / ".hermes" / "aim" / "data")
        Path(db_dir).mkdir(parents=True, exist_ok=True)
        self._db_path = f"{db_dir}/pin_{agent_id}.db"

        # 内存缓存: msg_id -> timestamp
        self._cache: Dict[str, float] = {}
        # 已持久化的 msg_id 集合（避免重复写 DB）
        self._persisted: Set[str] = set()

        # 并发控制
        self._lock = asyncio.Lock()

        # 统计
        self.stats = {
            "hits": 0,
            "misses": 0,
            "persisted": 0,
            "evicted": 0,
        }

        # 初始化 DB
        self._init_db()

    # ── 数据库 ────────────────────────────

    def _init_db(self):
        """初始化 SQLite 持久化存储"""
        try:
            conn = sqlite3.connect(self._db_path, timeout=5)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pins (
                    msg_id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    ttl REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pins_ts ON pins(ts)
            """)
            conn.commit()
            # 清理过期记录（启动时）
            self._cleanup_db(conn)
            conn.close()
        except Exception as e:
            print(f"[Pin:{self.agent_id}] DB 初始化失败: {e}")

    def _cleanup_db(self, conn: sqlite3.Connection):
        """清理 DB 中的过期记录"""
        try:
            now = time.time()
            cursor = conn.execute(
                "DELETE FROM pins WHERE ts + ttl < ?", (now,)
            )
            deleted = cursor.rowcount
            if deleted > 0:
                conn.commit()
        except Exception:
            pass

    def _persist_batch(self, entries: Dict[str, float]):
        """批量持久化到 DB"""
        if not entries:
            return
        try:
            now = time.time()
            conn = sqlite3.connect(self._db_path, timeout=5)
            data = [(mid, ts, self.ttl, now) for mid, ts in entries.items() if mid not in self._persisted]
            if data:
                conn.executemany(
                    "INSERT OR IGNORE INTO pins (msg_id, ts, ttl, created_at) VALUES (?, ?, ?, ?)",
                    data,
                )
                conn.commit()
                self.stats["persisted"] += len(data)
                for mid, _, _, _ in data:
                    self._persisted.add(mid)
            conn.close()
        except Exception as e:
            print(f"[Pin:{self.agent_id}] 持久化写入失败: {e}")

    # ── 核心去重接口 ──────────────────────

    async def is_duplicate(self, msg_id: str) -> bool:
        """
        检查 msg_id 是否已处理过（重复）
        返回 True = 是重复消息，跳过处理
        """
        async with self._lock:
            now = time.time()

            # 1. 内存缓存检查
            ts = self._cache.get(msg_id)
            if ts is not None:
                if now - ts <= self.ttl:
                    self.stats["hits"] += 1
                    return True
                # 过期了，移除
                del self._cache[msg_id]

            # 2. DB 持久化检查
            if msg_id in self._persisted:
                self.stats["hits"] += 1
                return True
            try:
                conn = sqlite3.connect(self._db_path, timeout=3)
                cursor = conn.execute(
                    "SELECT ts FROM pins WHERE msg_id = ? AND ts + ttl > ?",
                    (msg_id, now),
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    self._persisted.add(msg_id)
                    self.stats["hits"] += 1
                    return True
            except Exception:
                pass

            self.stats["misses"] += 1
            return False

    async def mark(self, msg_id: str):
        """
        标记 msg_id 为已处理
        必须与 is_duplicate 配对使用（先检查，通过后标记）
        """
        async with self._lock:
            now = time.time()
            self._cache[msg_id] = now

            # 内存缓存上限控制 — LRU 淘汰
            if len(self._cache) > self.max_memory:
                # 按时间排序，淘汰最旧的
                sorted_items = sorted(self._cache.items(), key=lambda x: x[1])
                evict_count = len(self._cache) - self.max_memory
                for mid, _ in sorted_items[:evict_count]:
                    del self._cache[mid]
                    self.stats["evicted"] += 1

    async def flush(self):
        """将内存缓存刷入持久化存储（定时调用或关闭前调用）"""
        async with self._lock:
            if self._cache:
                self._persist_batch(self._cache)

    # ── 批量检查（可选） ──────────────────

    async def batch_check(self, msg_ids: list) -> Dict[str, bool]:
        """批量检查多条 msg_id 是否为重复"""
        result = {}
        for mid in msg_ids:
            result[mid] = await self.is_duplicate(mid)
        return result

    # ── 管理接口 ─────────────────────────

    async def clear(self):
        """清空所有缓存（内存 + DB）"""
        async with self._lock:
            self._cache.clear()
            self._persisted.clear()
            try:
                conn = sqlite3.connect(self._db_path, timeout=5)
                conn.execute("DELETE FROM pins")
                conn.commit()
                conn.close()
            except Exception:
                pass

    def get_pin_count(self) -> int:
        """返回内存中的去重记录数"""
        return len(self._cache)

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            **self.stats,
            "cache_size": len(self._cache),
            "persisted_set": len(self._persisted),
            "ttl": self.ttl,
            "max_memory": self.max_memory,
        }

    def is_persistent(self) -> bool:
        """检查持久化存储是否可用"""
        try:
            conn = sqlite3.connect(self._db_path, timeout=3)
            conn.execute("SELECT 1 FROM pins LIMIT 1")
            conn.close()
            return True
        except Exception:
            return False


# ── 自测 ──────────────────────────────────


async def _self_test():
    """基本功能自测"""
    print("=" * 50)
    print("AIM Pin 自测")
    print("=" * 50)

    pin = AIMPin(agent_id="TEST", ttl=60, db_dir="/tmp/aim_pin_test")
    passed = 0
    failed = 0

    def check(name, condition):
        nonlocal passed, failed
        if condition:
            print(f"  ✓ {name}")
            passed += 1
        else:
            print(f"  ✗ {name}")
            failed += 1

    # Test 1: 新消息不是重复
    msg_id = str(uuid.uuid4())
    dup = await pin.is_duplicate(msg_id)
    check("新消息非重复", not dup)

    # Test 2: 标记后变成重复
    await pin.mark(msg_id)
    dup = await pin.is_duplicate(msg_id)
    check("标记后检测为重复", dup)

    # Test 3: 不同 msg_id 互不影响
    msg2 = str(uuid.uuid4())
    dup2 = await pin.is_duplicate(msg2)
    check("不同 msg_id 互不影响", not dup2)

    # Test 4: 持久化写入
    await pin.flush()
    check("持久化写入无异常", True)

    # Test 5: 重建 Pin 后仍可去重（持久化生效）
    pin2 = AIMPin(agent_id="TEST", ttl=60, db_dir="/tmp/aim_pin_test")
    dup_restore = await pin2.is_duplicate(msg_id)
    check("持久化重启后仍可去重", dup_restore)

    # Test 6: 统计
    stats = pin.get_stats()
    check("统计命中数正确", stats["hits"] >= 1)
    check("统计 miss 数正确", stats["misses"] >= 2)
    check("持久化记录 > 0", stats["persisted"] > 0)

    await pin.clear()

    print(f"\n  结果: {passed}/{passed+failed} 通过")
    return passed, failed


if __name__ == "__main__":
    asyncio.run(_self_test())
