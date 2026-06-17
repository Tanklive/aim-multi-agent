#!/usr/bin/env python3
"""
AIM 消息去重模块 — LRU Cache + TTL
三个 Agent 统一使用，防止消息重复处理

用法:
    from msg_dedup import MessageDedup

    dedup = MessageDedup(max_size=100, ttl=300)
    if dedup.is_duplicate(msg_id):
        return  # 跳过
    dedup.mark_processed(msg_id)
"""

import json
import os
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock

# 持久化文件路径
DEDUP_DATA_DIR = Path(os.path.expanduser("~/.openclaw/aim/data"))
DEDUP_PERSIST_FILE = DEDUP_DATA_DIR / "seen_msgs.json"


class MessageDedup:
    """LRU 消息去重缓存（支持持久化）"""

    def __init__(self, max_size: int = 100, ttl: int = 300, persist: bool = True):
        """
        Args:
            max_size: 最大缓存条目数（LRU 淘汰）
            ttl: 条目过期时间（秒），默认 5 分钟
            persist: 是否持久化到文件（重启后恢复）
        """
        self.max_size = max_size
        self.ttl = ttl
        self.persist = persist
        self._cache: OrderedDict[str, float] = OrderedDict()  # msg_id -> timestamp
        self._lock = Lock()
        
        # 从文件恢复缓存
        if self.persist:
            self._load_from_file()

    def is_duplicate(self, msg_id: str) -> bool:
        """检查 msg_id 是否已处理过（未过期）"""
        if not msg_id:
            return False

        with self._lock:
            if msg_id in self._cache:
                ts = self._cache[msg_id]
                if time.time() - ts < self.ttl:
                    # 未过期，移到末尾（最近使用）
                    self._cache.move_to_end(msg_id)
                    return True
                else:
                    # 已过期，删除
                    del self._cache[msg_id]
            return False

    def mark_processed(self, msg_id: str):
        """标记 msg_id 为已处理"""
        if not msg_id:
            return

        with self._lock:
            if msg_id in self._cache:
                self._cache.move_to_end(msg_id)
            else:
                self._cache[msg_id] = time.time()
                # 超过上限，淘汰最旧的
                while len(self._cache) > self.max_size:
                    self._cache.popitem(last=False)
            # 持久化到文件
            if self.persist:
                self._save_to_file()

    def cleanup(self):
        """手动清理过期条目"""
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._cache.items() if now - v > self.ttl]
            for k in expired:
                del self._cache[k]

    @property
    def size(self) -> int:
        return len(self._cache)

    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "ttl": self.ttl,
            "persist": self.persist,
        }

    def _load_from_file(self):
        """从文件恢复缓存"""
        try:
            if DEDUP_PERSIST_FILE.exists():
                data = json.loads(DEDUP_PERSIST_FILE.read_text(encoding="utf-8"))
                now = time.time()
                # 只恢复未过期的条目
                for item in data:
                    msg_id = item.get("id", "")
                    ts = item.get("ts", 0)
                    if msg_id and (now - ts) < self.ttl:
                        self._cache[msg_id] = ts
                # 强制 LRU 淘汰
                while len(self._cache) > self.max_size:
                    self._cache.popitem(last=False)
        except Exception:
            pass  # 恢复失败不影响正常运行

    def _save_to_file(self):
        """持久化缓存到文件"""
        try:
            DEDUP_DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = [{"id": k, "ts": v} for k, v in self._cache.items()]
            DEDUP_PERSIST_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  # 保存失败不影响正常运行


# 全局单例（可选）
_default_dedup = None


def get_dedup(max_size: int = 100, ttl: int = 300) -> MessageDedup:
    """获取全局去重实例"""
    global _default_dedup
    if _default_dedup is None:
        _default_dedup = MessageDedup(max_size=max_size, ttl=ttl)
    return _default_dedup
