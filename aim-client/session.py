"""
SessionManager — AIM Client 会话生命周期管理

职责：
- 按 from_id 路由会话，每个来源独立 pool
- CLI 模式：管理 session 创建/复用/trim（复用 ≤5 次后重建）
- API Server 模式：仅跟踪 from_id，由 adapter 自行管理会话
- 提供 session_id 给 _call_adapter 注入协议

协议版本：ADAPTER-PROTOCOL v1.0
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionEntry:
    """单个会话记录"""
    session_id: str
    from_id: str
    pool_index: int        # pool:{from_id}:{n} 中的 n
    created_at: float      # UNIX timestamp
    last_used_at: float
    use_count: int = 0     # 复用次数
    mode: str = "cli"      # "cli" | "api_server"


@dataclass
class SessionPool:
    """一个 from_id 对应的会话池"""
    from_id: str
    mode: str = "cli"                    # "cli" | "api_server"
    max_reuse: int = 5                   # CLI 模式最大复用次数
    entries: Dict[int, SessionEntry] = field(default_factory=dict)
    _next_index: int = 0

    def next_index(self) -> int:
        idx = self._next_index
        self._next_index += 1
        return idx

    def get_or_create(self) -> SessionEntry:
        """获取当前活跃 session，或创建新的"""
        # CLI 模式：找复用次数未满的
        if self.mode == "cli":
            for entry in self.entries.values():
                if entry.use_count < self.max_reuse:
                    entry.use_count += 1
                    entry.last_used_at = time.time()
                    return entry

        # 创建新 session
        idx = self.next_index()
        session_id = f"pool:{self.from_id}:{idx}"
        entry = SessionEntry(
            session_id=session_id,
            from_id=self.from_id,
            pool_index=idx,
            created_at=time.time(),
            last_used_at=time.time(),
            use_count=1,
            mode=self.mode,
        )
        self.entries[idx] = entry
        return entry

    def trim(self, keep_count: int = 1) -> int:
        """清理旧 session，保留最近使用的 keep_count 个"""
        if len(self.entries) <= keep_count:
            return 0

        sorted_entries = sorted(
            self.entries.values(),
            key=lambda e: e.last_used_at,
            reverse=True,
        )
        removed = 0
        for entry in sorted_entries[keep_count:]:
            del self.entries[entry.pool_index]
            removed += 1
        return removed

    def health(self) -> bool:
        """检查池是否健康（有可用 session）"""
        return len(self.entries) > 0

    def stats(self) -> dict:
        return {
            "from_id": self.from_id,
            "mode": self.mode,
            "active_sessions": len(self.entries),
            "total_uses": sum(e.use_count for e in self.entries.values()),
        }


class SessionManager:
    """
    AIM Client 会话管理器

    用法:
        sm = SessionManager(mode="cli")  # 或 "api_server"

        # 获取 session_id
        sid = sm.get_session_id("ZS0002")

        # trim 旧 session
        sm.trim("ZS0002", keep=3)

        # 获取状态
        stats = sm.status()
    """

    def __init__(self, mode: str = "cli", max_reuse: int = 5):
        """
        Args:
            mode: "cli" 或 "api_server"
            max_reuse: CLI 模式下单个 session 最大复用次数
        """
        self.mode = mode
        self.max_reuse = max_reuse
        self._pools: Dict[str, SessionPool] = {}
        self._created_at = time.time()

    def get_session_id(self, from_id: str) -> str:
        """
        获取或创建 from_id 对应的 session_id

        规则:
        - CLI 模式：复用 ≤ max_reuse 次，超限自动新建
        - API Server 模式：每个 from_id 固定一个 session_id（adapter 自己管生命周期）

        Returns:
            session_id 字符串，如 "pool:ZS0002:3"
        """
        if from_id not in self._pools:
            self._pools[from_id] = SessionPool(
                from_id=from_id,
                mode=self.mode,
                max_reuse=self.max_reuse,
            )

        pool = self._pools[from_id]
        entry = pool.get_or_create()

        logger.debug(
            f"SessionManager: {from_id} → {entry.session_id} "
            f"(use #{entry.use_count}/{pool.max_reuse}, pool={len(pool.entries)})"
        )
        return entry.session_id

    def trim(self, from_id: str, keep_count: int = 1) -> int:
        """清理 from_id 的旧 session，保留最近使用的 keep_count 个"""
        if from_id not in self._pools:
            return 0
        removed = self._pools[from_id].trim(keep_count)
        if removed:
            logger.info(f"SessionManager: trimmed {removed} sessions for {from_id}")
        return removed

    def trim_all(self, keep_count: int = 1) -> int:
        """清理所有 from_id 的旧 session"""
        total = 0
        for pid in list(self._pools.keys()):
            total += self.trim(pid, keep_count)
        return total

    def reload(self, from_id: Optional[str] = None) -> None:
        """
        热刷新：标记 session 缓存失效

        CLI 模式：清空 from_id 对应池，下次 get_session_id 全新创建
        API Server 模式：不发信号，由 adapter reload action 处理
        """
        if self.mode == "api_server":
            logger.info(f"SessionManager: reload requested (api_server mode, no-op)")
            return

        if from_id:
            if from_id in self._pools:
                count = len(self._pools[from_id].entries)
                del self._pools[from_id]
                logger.info(f"SessionManager: reloaded {from_id} ({count} sessions cleared)")
        else:
            count = sum(len(p.entries) for p in self._pools.values())
            self._pools.clear()
            logger.info(f"SessionManager: reloaded all ({count} sessions cleared)")

    def cancel(self, from_id: str, session_id: str) -> bool:
        """
        取消进行中的会话（尽力而为语义）

        CLI 模式：从池中移除该 session
        API Server 模式：标记为已取消，adapter cancel action 处理

        Returns:
            True 如果 session 存在且被移除
        """
        if from_id not in self._pools:
            return False

        pool = self._pools[from_id]
        for idx, entry in list(pool.entries.items()):
            if entry.session_id == session_id:
                del pool.entries[idx]
                logger.info(f"SessionManager: cancelled {session_id}")
                return True
        return False

    def get_pool_stats(self, from_id: Optional[str] = None) -> dict:
        """获取会话池统计"""
        if from_id:
            pool = self._pools.get(from_id)
            return pool.stats() if pool else {"from_id": from_id, "active_sessions": 0}
        return {
            "total_from_ids": len(self._pools),
            "total_sessions": sum(len(p.entries) for p in self._pools.values()),
            "total_uses": sum(sum(e.use_count for e in p.entries.values()) for p in self._pools.values()),
        }

    def status(self) -> dict:
        """运行时指标"""
        return {
            "mode": self.mode,
            "max_reuse": self.max_reuse,
            "uptime_ms": int((time.time() - self._created_at) * 1000),
            "pools": self.get_pool_stats(),
        }
