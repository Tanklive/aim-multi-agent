"""
AIM 重传机制集成层

将 retry_components 的四大组件集成到 connection_pool + node.py

功能：
1. 增强 DeliveryTracker — 接入 RetryPolicy 退避策略
2. OfflineCache 集成 — Agent 离线时自动缓存
3. RetryEventEmitter 集成 — 所有重传事件推送到 Observer
4. on_agent_recovered — Agent 恢复时批量推送缓存
5. SuspectTracker 集成 — unreachable → suspect → dead 状态机

版本：v1.0
作者：呱呱 🐸
日期：2026-06-08
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

from retry_components import (
    RetryPolicy,
    OfflineCache,
    RetryEventEmitter,
    SeqReplayBuffer,
    SuspectTracker,
    SeqDeduplicator,
)

logger = logging.getLogger(__name__)


class RetryManager:
    """重传管理器 — 集成所有重传组件
    
    用法：
        retry_mgr = RetryManager(hub_ref=hub)
        # 在 node.py 初始化时创建
        # 在投递/断连/恢复时调用对应方法
    """
    
    def __init__(self, hub_ref: object = None, config: dict = None):
        """
        Args:
            hub_ref: Hub 引用（用于推送 Observer 事件）
            config: 配置覆盖
        """
        config = config or {}
        
        # 核心组件
        self.policy = RetryPolicy(
            backoff_seconds=config.get("backoff_seconds", [10.0, 30.0, 60.0]),
            jitter=config.get("jitter", 2.0),
            max_retries=config.get("max_retries", 3),
            ack_timeout=config.get("ack_timeout", 30.0),
        )
        self.offline_cache = OfflineCache()
        self.emitter = RetryEventEmitter(hub_ref=hub_ref)
        self.replay_buffer = SeqReplayBuffer(capacity=config.get("replay_capacity", 1000))
        self.suspect_tracker = SuspectTracker(
            suspect_ttl_ms=config.get("suspect_ttl_ms", 300000)
        )
        self.dedup = SeqDeduplicator(window_size=config.get("dedup_window", 1000))
        
        # 外部回调（由 node.py 注入）
        self._do_deliver: Optional[Callable] = None  # async (msg, agent_id) -> bool
        self._get_connection: Optional[Callable] = None  # (agent_id) -> ConnectionInfo | None
        self._notify_sender: Optional[Callable] = None  # async (msg_id, status, detail)
        
        # 进行中的重试任务
        self._retry_tasks: Dict[str, asyncio.Task] = {}  # msg_id -> task
        
        logger.info("RetryManager 初始化完成")
    
    def set_callbacks(self, do_deliver: Callable = None,
                      get_connection: Callable = None,
                      notify_sender: Callable = None):
        """注入外部回调"""
        if do_deliver:
            self._do_deliver = do_deliver
        if get_connection:
            self._get_connection = get_connection
        if notify_sender:
            self._notify_sender = notify_sender
    
    # ============================================================
    # 消息投递（带重试）
    # ============================================================
    
    async def deliver_with_retry(self, msg: dict, target_agent_id: str,
                                  from_agent: str = "") -> dict:
        """带重试的消息投递
        
        流程：
        1. 检查目标在线状态
        2. 离线 → 入 OfflineCache
        3. 在线 → 投递 + 启动退避重试
        
        Returns:
            {"status": "delivering"|"cached", "msg_id": str}
        """
        msg_id = msg.get("msg_id", "")
        
        # 检查目标连接
        conn = self._get_connection(target_agent_id) if self._get_connection else None
        
        if conn is None:
            # 离线 → 缓存
            result = self.offline_cache.enqueue(target_agent_id, msg)
            
            if result["status"] == "dropped_oldest":
                await self.emitter.emit_cache_overflow(
                    target_agent_id,
                    result["dropped_msg_id"],
                    result["queue_size"]
                )
            
            logger.info(f"📦 Agent {target_agent_id} 离线，消息已缓存 (queue={result['queue_size']})")
            return {"status": "cached", "msg_id": msg_id, "queue_size": result["queue_size"]}
        
        # 在线 → 投递
        if self._do_deliver:
            success = await self._do_deliver(msg, target_agent_id)
            if not success:
                # 投递失败，转缓存
                result = self.offline_cache.enqueue(target_agent_id, msg)
                await self.emitter.emit_unreachable(target_agent_id, msg_id)
                return {"status": "cached", "msg_id": msg_id}
        
        # 启动退避重试任务
        task = asyncio.create_task(
            self._retry_loop(msg, target_agent_id, from_agent)
        )
        self._retry_tasks[msg_id] = task
        
        return {"status": "delivering", "msg_id": msg_id}
    
    async def _retry_loop(self, msg: dict, target_agent_id: str,
                          from_agent: str = ""):
        """退避重试循环"""
        msg_id = msg.get("msg_id", "")
        
        for attempt in range(self.policy.max_retries):
            # 等待退避
            delay = self.policy.get_delay(attempt)
            if delay < 0:
                break
            
            await asyncio.sleep(delay)
            
            # 发射 retry 事件
            await self.emitter.emit_retry(
                target_agent_id, msg_id,
                attempt + 1, delay, from_agent
            )
            
            # 检查目标是否在线
            conn = self._get_connection(target_agent_id) if self._get_connection else None
            if conn is None:
                # 离线了 → 切到 OfflineCache
                self.offline_cache.enqueue(target_agent_id, msg)
                await self.emitter.emit_unreachable(
                    target_agent_id, msg_id,
                    transition="to_offline_cache"
                )
                # 标记 suspect
                self.suspect_tracker.mark_unreachable(target_agent_id)
                return
            
            # 重传
            if self._do_deliver:
                success = await self._do_deliver(msg, target_agent_id)
                if success:
                    await self.emitter.emit_delivered(
                        target_agent_id, msg_id,
                        attempt + 1, delay * 1000
                    )
                    return
        
        # 3 次全失败 → expired
        await self.emitter.emit_expired(
            target_agent_id, msg_id,
            reason="max_retries_exceeded",
            attempts=self.policy.max_retries,
            from_agent=from_agent
        )
        
        # 通知发送方
        if self._notify_sender:
            await self._notify_sender(msg_id, "delivery_failed", {
                "reason": "max_retries_exceeded",
                "attempts": self.policy.max_retries,
            })
        
        # 清理
        self._retry_tasks.pop(msg_id, None)
    
    # ============================================================
    # ACK 处理
    # ============================================================
    
    def on_ack_received(self, msg_id: str, agent_id: str = ""):
        """收到 ACK — 取消重试任务"""
        task = self._retry_tasks.pop(msg_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"✅ ACK received, cancelled retry for msg={msg_id}")
    
    def on_processing_ack_received(self, msg_id: str, agent_id: str = ""):
        """收到 processing_ack — 取消重传计时器，保留 tracking"""
        task = self._retry_tasks.pop(msg_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"✅ processing_ack received, cancelled retry for msg={msg_id}")
    
    # ============================================================
    # Agent 恢复
    # ============================================================
    
    async def on_agent_recovered(self, agent_id: str) -> dict:
        """Agent 恢复上线回调
        
        1. 取出所有缓存消息
        2. 发射 recovered 事件
        3. 批量推送缓存消息
        
        Returns:
            {"cached_count": int, "flush_duration_ms": float}
        """
        start_time = time.time()
        
        # 清除 suspect 状态
        suspect_duration = self.suspect_tracker.clear_suspect(agent_id)
        
        # 取出缓存
        cached_msgs = self.offline_cache.dequeue_all(agent_id)
        cached_count = len(cached_msgs)
        
        if cached_count == 0:
            await self.emitter.emit_recovered(agent_id, 0)
            return {"cached_count": 0, "flush_duration_ms": 0}
        
        cached_msg_ids = [m.get("msg_id", "?") for m in cached_msgs]
        
        # 批量推送
        flushed = 0
        for msg in cached_msgs:
            conn = self._get_connection(agent_id) if self._get_connection else None
            if conn is None:
                # 又离线了，把剩下的放回去
                for remaining in cached_msgs[flushed:]:
                    self.offline_cache.enqueue(agent_id, remaining)
                logger.warning(f"⚠️ Agent {agent_id} 中途离线，{cached_count - flushed} 条消息回缓存")
                break
            
            if self._do_deliver:
                await self._do_deliver(msg, agent_id)
            flushed += 1
        
        flush_duration_ms = (time.time() - start_time) * 1000
        
        # 发射 recovered 事件
        await self.emitter.emit_recovered(
            agent_id, cached_count,
            cached_msg_ids[:flushed],
            flush_duration_ms
        )
        
        logger.info(
            f"🔄 Agent {agent_id} recovered: flushed {flushed}/{cached_count} "
            f"messages in {flush_duration_ms:.0f}ms"
        )
        
        return {
            "cached_count": cached_count,
            "flushed": flushed,
            "flush_duration_ms": flush_duration_ms,
        }
    
    # ============================================================
    # Suspect 检查（定时调用）
    # ============================================================
    
    async def check_suspects(self):
        """检查 suspect 超时，返回 dead agent 列表
        
        应由定时任务定期调用（如每 30s）
        """
        dead_agents = self.suspect_tracker.check_expired()
        
        for agent_id in dead_agents:
            await self.emitter.emit_expired(
                agent_id, "",
                reason="suspect_ttl_exceeded",
                attempts=0
            )
        
        return dead_agents
    
    # ============================================================
    # 断连回放
    # ============================================================
    
    def push_to_replay(self, msg: dict) -> int:
        """推入回放缓冲"""
        return self.replay_buffer.push(msg)
    
    def replay_for_agent(self, last_seq: int) -> list:
        """为重连 Agent 回放积压消息"""
        return self.replay_buffer.replay(last_seq)
    
    def is_seq_stale(self, last_seq: int) -> bool:
        """检查 seq 是否过旧"""
        return self.replay_buffer.is_seq_stale(last_seq)
    
    # ============================================================
    # 去重
    # ============================================================
    
    def is_duplicate(self, agent_id: str, seq_id: str) -> bool:
        """检查 sequenceId 是否重复"""
        return self.dedup.is_duplicate(agent_id, seq_id)
    
    def mark_seen(self, agent_id: str, seq_id: str):
        """标记 sequenceId 已处理"""
        self.dedup.mark_seen(agent_id, seq_id)
    
    # ============================================================
    # 状态查询
    # ============================================================
    
    def get_status(self) -> dict:
        """获取重传管理器状态"""
        return {
            "policy": {
                "backoff_seconds": self.policy.backoff_seconds,
                "max_retries": self.policy.max_retries,
                "jitter": self.policy.jitter,
            },
            "offline_cache": self.offline_cache.get_all_pending(),
            "suspects": self.suspect_tracker.get_all_suspects(),
            "active_retries": len(self._retry_tasks),
            "replay_buffer_seq": self.replay_buffer.get_current_seq(),
            "recent_events": self.emitter.get_recent_events(5),
        }
