"""
AIM 重传机制核心组件

四大组件：
1. RetryPolicy      — 重试策略（阶梯退避 + 抖动）
2. OfflineCache      — 离线消息缓存（分层优先级）
3. RetryEventEmitter — 重传事件发射器（推送到 Observer 通道）
4. SeqReplayBuffer   — 断连回放（seq 缓存 + 回放）

版本：v1.0
作者：呱呱 🐸
日期：2026-06-08
基于：retry-design.md + observer-interface-spec.md
"""

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 1. RetryPolicy — 重试策略
# ============================================================

@dataclass
class RetryPolicy:
    """重试策略 — 阶梯退避 + 随机抖动
    
    默认配置：
    - 3 级退避: 10s → 30s → 60s
    - 抖动: ±2s
    - 最大重试: 3 次
    - 单次 ACK 超时: 30s
    """
    backoff_seconds: List[float] = field(default_factory=lambda: [10.0, 30.0, 60.0])
    jitter: float = 2.0
    max_retries: int = 3
    ack_timeout: float = 30.0
    
    def get_delay(self, retry_count: int) -> float:
        """获取第 N 次退避延迟（含抖动）
        
        Returns:
            延迟秒数，-1 表示已达最大重试次数
        """
        if retry_count >= len(self.backoff_seconds):
            return -1
        base = self.backoff_seconds[retry_count]
        jitter = random.uniform(-self.jitter, self.jitter)
        return max(0.5, base + jitter)  # 最小 0.5s 防止负数
    
    def is_max_retries(self, retry_count: int) -> bool:
        """检查是否已达最大重试次数"""
        return retry_count >= self.max_retries


# ============================================================
# 2. OfflineCache — 离线消息缓存
# ============================================================

class OfflineCache:
    """Agent 离线消息缓存
    
    按消息类型分级：
    - 高优先 (task/command/directive): 100 条
    - 低优先 (heartbeat/status): 20 条
    
    顺序保持: 同类 FIFO，跨类高优先先出
    超限策略: 丢弃同优先级最旧 + 日志 + Observer 通知
    """
    
    MSG_TYPE_CLASSIFICATION = {
        "heartbeat": "low",
        "status": "low",
        "task": "high",
        "command": "high",
        "directive": "high",
        "message": "high",
        "text": "high",
    }
    
    MAX_SIZE_BY_PRIORITY = {
        "low": 20,
        "high": 100,
    }
    
    def __init__(self):
        self._caches: Dict[str, Dict[str, deque]] = {}  # agent_id -> {"high": deque, "low": deque}
    
    def _classify(self, msg: dict) -> str:
        """根据消息类型分类优先级"""
        msg_type = msg.get("type", msg.get("msg_type", ""))
        return self.MSG_TYPE_CLASSIFICATION.get(msg_type, "high")  # 默认高优先（安全策略）
    
    def _get_cache(self, agent_id: str) -> Dict[str, deque]:
        """获取或创建 Agent 的缓存"""
        if agent_id not in self._caches:
            self._caches[agent_id] = {
                "high": deque(maxlen=self.MAX_SIZE_BY_PRIORITY["high"]),
                "low": deque(maxlen=self.MAX_SIZE_BY_PRIORITY["low"]),
            }
        return self._caches[agent_id]
    
    def enqueue(self, agent_id: str, msg: dict) -> dict:
        """入队消息到离线缓存
        
        Returns:
            {
                "status": "cached" | "dropped_oldest",
                "queue_size": int,
                "priority": "high" | "low",
                "dropped_msg_id": str | None
            }
        """
        priority = self._classify(msg)
        cache = self._get_cache(agent_id)
        target_queue = cache[priority]
        max_size = self.MAX_SIZE_BY_PRIORITY[priority]
        
        dropped = None
        dropped_msg_id = None
        
        # 检查是否需要丢弃最旧
        if len(target_queue) >= max_size:
            dropped = target_queue[0]  # deque 自动淘汰，但我们需要记录
            dropped_msg_id = dropped.get("msg_id", "?")
            logger.warning(
                f"离线缓存超限，丢弃最旧消息: agent={agent_id}, "
                f"priority={priority}, dropped_msg_id={dropped_msg_id}"
            )
        
        target_queue.append(msg)
        
        return {
            "status": "dropped_oldest" if dropped else "cached",
            "queue_size": len(target_queue),
            "priority": priority,
            "dropped_msg_id": dropped_msg_id,
        }
    
    def dequeue_all(self, agent_id: str) -> list:
        """取出指定 Agent 的所有缓存消息（高优先先出）"""
        cache = self._caches.pop(agent_id, {"high": deque(), "low": deque()})
        # 高优先先出，保持同类 FIFO
        result = list(cache["high"]) + list(cache["low"])
        return result
    
    def get_queue_size(self, agent_id: str) -> dict:
        """获取指定 Agent 的缓存队列大小"""
        cache = self._caches.get(agent_id, {"high": deque(), "low": deque()})
        return {
            "high": len(cache["high"]),
            "low": len(cache["low"]),
            "total": len(cache["high"]) + len(cache["low"]),
        }
    
    def get_all_pending(self) -> dict:
        """获取所有有缓存的 Agent 摘要"""
        return {
            agent: {
                "high": len(q["high"]),
                "low": len(q["low"]),
                "total": len(q["high"]) + len(q["low"]),
            }
            for agent, q in self._caches.items()
            if q["high"] or q["low"]
        }
    
    def clear(self, agent_id: str):
        """清除指定 Agent 的缓存"""
        self._caches.pop(agent_id, None)


# ============================================================
# 3. RetryEventEmitter — 重传事件发射器
# ============================================================

@dataclass
class RetryEventEmitter:
    """重传事件发射器 — 推送到 Observer 通道
    
    事件格式（与 observer-interface-spec.md 对齐）:
    {cmd, seq, timestamp, agent_id, payload}
    
    cmd 枚举:
      - retry_event    — 重传触发
      - delivery_event — 投递状态变更
      - cache_event    — 离线缓存相关
    """
    hub_ref: object = None  # Hub 引用，用于推送
    max_event_queue: int = 100
    _seq: int = field(default=0, init=False)
    event_queue: deque = field(default_factory=lambda: deque(maxlen=100), init=False)
    
    def _get_next_seq(self) -> int:
        """获取下一个全局 seq"""
        self._seq += 1
        return self._seq
    
    def set_hub_ref(self, hub_ref):
        """注入 Hub 引用"""
        self.hub_ref = hub_ref
    
    async def emit_retry(self, target_agent_id: str, msg_id: str,
                         attempt: int, delay_seconds: float,
                         from_agent: str = ""):
        """发射 retry 事件 — 重传触发"""
        event = {
            "cmd": "retry_event",
            "seq": self._get_next_seq(),
            "timestamp": time.time(),
            "agent_id": target_agent_id,
            "payload": {
                "type": "retry",
                "msg_id": msg_id,
                "attempt": attempt,
                "delay_seconds": round(delay_seconds, 2),
                "next_retry_at": round(time.time() + delay_seconds, 3),
                "target_agent": target_agent_id,
                "from": from_agent,
                "reason": "no_ack",
            },
        }
        self.event_queue.append(event)
        await self._broadcast(event)
        logger.info(f"📡 retry_event: msg={msg_id}, attempt={attempt}, delay={delay_seconds:.1f}s")
    
    async def emit_expired(self, target_agent_id: str, msg_id: str,
                           reason: str, attempts: int = 0,
                           from_agent: str = ""):
        """发射 expired 事件 — 消息过期/缓存溢出"""
        event = {
            "cmd": "delivery_event",
            "seq": self._get_next_seq(),
            "timestamp": time.time(),
            "agent_id": target_agent_id,
            "payload": {
                "type": "expired",
                "msg_id": msg_id,
                "reason": reason,
                "total_attempts": attempts,
                "target_agent": target_agent_id,
                "from": from_agent,
            },
        }
        self.event_queue.append(event)
        await self._broadcast(event)
        logger.info(f"📡 delivery_event(expired): msg={msg_id}, reason={reason}")
    
    async def emit_recovered(self, agent_id: str, cached_count: int,
                             cached_msg_ids: list = None,
                             flush_duration_ms: float = 0):
        """发射 recovered 事件 — Agent 恢复在线"""
        event = {
            "cmd": "cache_event",
            "seq": self._get_next_seq(),
            "timestamp": time.time(),
            "agent_id": agent_id,
            "payload": {
                "type": "recovered",
                "target_agent": agent_id,
                "cached_count": cached_count,
                "cached_msg_ids": cached_msg_ids or [],
                "flush_duration_ms": round(flush_duration_ms, 1),
            },
        }
        self.event_queue.append(event)
        await self._broadcast(event)
        logger.info(f"📡 cache_event(recovered): agent={agent_id}, cached={cached_count}")
    
    async def emit_unreachable(self, target_agent_id: str, msg_id: str,
                               transition: str = "to_offline_cache"):
        """发射 unreachable 事件 — 目标不可达"""
        event = {
            "cmd": "delivery_event",
            "seq": self._get_next_seq(),
            "timestamp": time.time(),
            "agent_id": target_agent_id,
            "payload": {
                "type": "unreachable",
                "msg_id": msg_id,
                "target_agent": target_agent_id,
                "transition": transition,
            },
        }
        self.event_queue.append(event)
        await self._broadcast(event)
        logger.info(f"📡 delivery_event(unreachable): agent={target_agent_id}, msg={msg_id}")
    
    async def emit_delivered(self, target_agent_id: str, msg_id: str,
                             attempt: int = 1, latency_ms: float = 0):
        """发射 delivered 事件 — 投递成功"""
        event = {
            "cmd": "delivery_event",
            "seq": self._get_next_seq(),
            "timestamp": time.time(),
            "agent_id": target_agent_id,
            "payload": {
                "type": "delivered",
                "msg_id": msg_id,
                "target_agent": target_agent_id,
                "attempt": attempt,
                "latency_ms": round(latency_ms, 1),
            },
        }
        self.event_queue.append(event)
        await self._broadcast(event)
        logger.info(f"📡 delivery_event(delivered): msg={msg_id}, attempt={attempt}")
    
    async def emit_cache_overflow(self, agent_id: str, dropped_msg_id: str,
                                  cache_size: int):
        """发射 cache_overflow 事件 — 缓存超限丢弃"""
        event = {
            "cmd": "cache_event",
            "seq": self._get_next_seq(),
            "timestamp": time.time(),
            "agent_id": agent_id,
            "payload": {
                "type": "cache_overflow",
                "target_agent": agent_id,
                "dropped_msg_id": dropped_msg_id,
                "cache_size": cache_size,
            },
        }
        self.event_queue.append(event)
        await self._broadcast(event)
        logger.warning(f"📡 cache_event(overflow): agent={agent_id}, dropped={dropped_msg_id}")
    
    async def _broadcast(self, event: dict):
        """推送到 Observer 通道"""
        if self.hub_ref and hasattr(self.hub_ref, 'broadcast_to_observers'):
            try:
                await self.hub_ref.broadcast_to_observers(
                    event.get("agent_id", "system"),
                    event
                )
            except Exception as e:
                logger.error(f"Observer broadcast failed: {e}")
    
    def get_recent_events(self, count: int = 10) -> list:
        """获取最近的事件（调试用）"""
        return list(self.event_queue)[-count:]


# ============================================================
# 4. SeqReplayBuffer — 断连回放缓冲
# ============================================================

@dataclass
class SeqReplayBuffer:
    """断连回放缓冲区 — seq 缓存 + 回放
    
    功能：
    - 缓存最近 N 条 Observer 消息（带 seq）
    - Agent 重连时从 last_seq + 1 开始回放
    - 超出容量丢弃最旧
    
    容量：1000 条（覆盖最坏情况）
    """
    capacity: int = 1000
    _buffer: deque = field(default_factory=lambda: deque(maxlen=1000), init=False)
    _current_seq: int = field(default=0, init=False)
    
    def push(self, msg: dict) -> int:
        """推入消息，返回分配的 seq"""
        self._current_seq += 1
        msg["seq"] = self._current_seq
        self._buffer.append(msg)
        return self._current_seq
    
    def get_current_seq(self) -> int:
        """获取当前最大 seq"""
        return self._current_seq
    
    def replay(self, last_seq: int) -> list:
        """从 last_seq + 1 开始回放积压消息
        
        Args:
            last_seq: 上次收到的 seq
            
        Returns:
            需要回放的消息列表（按 seq 升序）
        """
        if last_seq >= self._current_seq:
            return []  # 无积压
        
        result = []
        for msg in self._buffer:
            msg_seq = msg.get("seq", 0)
            if msg_seq > last_seq:
                result.append(msg)
        
        return result
    
    def is_seq_stale(self, last_seq: int) -> bool:
        """检查 last_seq 是否过旧（已被淘汰）"""
        if not self._buffer:
            return False
        oldest_seq = self._buffer[0].get("seq", 0)
        return last_seq < oldest_seq
    
    def clear(self):
        """清空缓冲区"""
        self._buffer.clear()
        self._current_seq = 0


# ============================================================
# 5. SuspectTracker — 可达性状态跟踪
# ============================================================

@dataclass
class SuspectTracker:
    """跟踪 Agent 可达性状态
    
    状态机: online → unreachable → suspect → dead
    TTL: suspect 超 5min 升级为 dead
    """
    suspect_ttl_ms: int = 300000  # 5 分钟
    _suspects: Dict[str, float] = field(default_factory=dict, init=False)
    
    def mark_unreachable(self, agent_id: str):
        """标记 Agent 为 unreachable（进入 suspect 状态）"""
        if agent_id not in self._suspects:
            self._suspects[agent_id] = time.time()
            logger.warning(f"⚠️ Agent {agent_id} marked as suspect (unreachable)")
    
    def clear_suspect(self, agent_id: str) -> Optional[float]:
        """清除 suspect 标记，返回持续时长(ms)"""
        start = self._suspects.pop(agent_id, None)
        if start:
            duration_ms = (time.time() - start) * 1000
            logger.info(f"✅ Agent {agent_id} suspect cleared (duration={duration_ms:.0f}ms)")
            return duration_ms
        return None
    
    def check_expired(self) -> list:
        """检查超时的 suspect，返回 dead agent 列表"""
        now = time.time()
        dead = []
        for agent_id, ts in list(self._suspects.items()):
            if (now - ts) * 1000 > self.suspect_ttl_ms:
                dead.append(agent_id)
                logger.error(f"💀 Agent {agent_id} suspect TTL expired → dead")
        return dead
    
    def is_suspect(self, agent_id: str) -> bool:
        """检查 Agent 是否在 suspect 状态"""
        return agent_id in self._suspects
    
    def get_suspect_duration(self, agent_id: str) -> Optional[float]:
        """获取 suspect 持续时长(ms)"""
        ts = self._suspects.get(agent_id)
        if ts:
            return (time.time() - ts) * 1000
        return None
    
    def get_all_suspects(self) -> dict:
        """获取所有 suspect Agent 及其持续时长"""
        now = time.time()
        return {
            agent_id: round((now - ts) * 1000, 0)
            for agent_id, ts in self._suspects.items()
        }


# ============================================================
# 6. SeqDeduplicator — sequenceId 去重窗口
# ============================================================

class SeqDeduplicator:
    """滑动窗口去重，O(1) 插入/查询
    
    每个 Agent 独立窗口，容量 1000
    deque(maxlen=1000) 自动淘汰最旧 seq
    """
    
    def __init__(self, window_size: int = 1000):
        self.window_size = window_size
        self._windows: Dict[str, deque] = {}
    
    def is_duplicate(self, agent_id: str, seq_id: str) -> bool:
        """检查 seq_id 是否已处理过"""
        window = self._windows.get(agent_id)
        if window is None:
            return False
        return seq_id in window
    
    def mark_seen(self, agent_id: str, seq_id: str):
        """记录已处理的 seq_id"""
        if agent_id not in self._windows:
            self._windows[agent_id] = deque(maxlen=self.window_size)
        self._windows[agent_id].append(seq_id)
    
    def clear(self, agent_id: str):
        """清除指定 Agent 的去重窗口"""
        self._windows.pop(agent_id, None)
    
    def get_window_size(self, agent_id: str) -> int:
        """获取指定 Agent 的窗口大小"""
        window = self._windows.get(agent_id)
        return len(window) if window else 0
