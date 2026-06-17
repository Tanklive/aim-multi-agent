"""
AIM Connection Pool — 连接池管理

V2 核心模块：支持多连接共存、Channel 机制、Handler 选举

版本：v0.1
作者：呱呱 🐸
日期：2026-06-06
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class MessageRingBuffer:
    """消息环形缓冲区 — 高效去重
    
    维护一个固定容量的 msg_id 集合，带 FIFO 老化。
    超过容量时自动淘汰最旧的条目。
    
    Features:
    - O(1) 插入和查找
    - 自动按插入顺序老化
    - 线程安全（asyncio 环境无需显式锁）
    - 可遍历
    """
    
    def __init__(self, capacity: int = 500):
        self.capacity = capacity
        self._set: set = set()
        self._queue: list = []  # 有序 FIFO，存储 msg_id
    
    def add(self, msg_id: str) -> bool:
        """添加 msg_id，返回 True 表示是新消息，False 表示重复"""
        if msg_id in self._set:
            return False  # 重复
        
        # 新消息
        self._set.add(msg_id)
        self._queue.append(msg_id)
        
        # 超容量淘汰最旧
        while len(self._queue) > self.capacity:
            oldest = self._queue.pop(0)
            self._set.discard(oldest)
        
        return True
    
    def __contains__(self, msg_id: str) -> bool:
        return msg_id in self._set
    
    def __len__(self) -> int:
        return len(self._set)
    
    def clear(self):
        """清空缓冲区"""
        self._set.clear()
        self._queue.clear()
    
    def to_list(self) -> list:
        """导出为有序列表（用于调试/状态查询）"""
        return list(self._queue)


class DeliveryTracker:
    """投递追踪器 — 30s 无 ACK 自动重推
    
    每个待确认的消息记录：
    - msg_id: 消息 ID
    - targets: 投递目标（agent_id 列表）
    - retries: 已重推次数
    - last_delivery: 上次投递时间
    - confirmed: 已收到 ACK（已确认送达）
    - processed: 已收到 processing_ack（AI 已开始处理）
    """
    
    def __init__(self, max_retries: int = 3, ack_timeout: float = 30.0):
        self._pending: Dict[str, dict] = {}
        self.max_retries = max_retries
        self.ack_timeout = ack_timeout
        self._timers: Dict[str, asyncio.Task] = {}  # msg_id -> timer task
    
    def track(self, msg_id: str, targets: list, msg_data: dict):
        """开始追踪一条消息的投递确认"""
        if msg_id in self._pending:
            return  # 已在追踪
        self._pending[msg_id] = {
            "msg_id": msg_id,
            "targets": list(targets),
            "retries": 0,
            "last_delivery": time.time(),
            "confirmed": False,
            "processed": False,
            "msg_data": msg_data,
        }
    
    def confirm_ack(self, msg_id: str, agent_id: str = "") -> bool:
        """收到 ACK，标记为已送达"""
        entry = self._pending.get(msg_id)
        if not entry:
            return False
        entry["confirmed"] = True
        if not entry["processed"]:
            # 已送达但未处理，等待 processing_ack
            pass
        return True
    
    def confirm_processed(self, msg_id: str, agent_id: str = "") -> bool:
        """收到 processing_ack，标记为已处理，可以停止追踪"""
        entry = self._pending.pop(msg_id, None)
        if not entry:
            return False
        entry["processed"] = True
        # 取消对应 timer
        timer = self._timers.pop(msg_id, None)
        if timer and not timer.done():
            timer.cancel()
        return True
    
    def needs_retry(self, msg_id: str) -> bool:
        """检查是否需要重推"""
        entry = self._pending.get(msg_id)
        if not entry:
            return False
        if entry["confirmed"] and not entry["processed"]:
            # 已送达但未处理，等待 processing_ack
            elapsed = time.time() - entry["last_delivery"]
            if elapsed > self.ack_timeout:
                # 超时，重推（但已送达 ACK 确认了，可能是 AI 处理延迟）
                # 对于已送达但未处理：不重推整个消息，只是记录告警
                return False
        if not entry["confirmed"]:
            elapsed = time.time() - entry["last_delivery"]
            if elapsed > self.ack_timeout and entry["retries"] < self.max_retries:
                return True
        return False
    
    def get_retry_delay(self, msg_id: str) -> float:
        """获取重推等待时间"""
        entry = self._pending.get(msg_id)
        if not entry:
            return 0
        if entry["retries"] >= self.max_retries:
            return -1  # 已达最大重试次数
        # 首次 10s，后续 15s
        return 10.0 if entry["retries"] == 0 else 15.0
    
    def mark_retried(self, msg_id: str) -> int:
        """标记为已重推，返回当前重试次数"""
        entry = self._pending.get(msg_id)
        if not entry:
            return 0
        entry["retries"] += 1
        entry["last_delivery"] = time.time()
        entry["confirmed"] = False  # 需要重新确认
        return entry["retries"]
    
    def remove(self, msg_id: str):
        """主动移除追踪（不等待）"""
        self._pending.pop(msg_id, None)
        timer = self._timers.pop(msg_id, None)
        if timer and not timer.done():
            timer.cancel()
    
    def get_pending(self) -> list:
        """获取所有待确认消息摘要"""
        return [
            {
                "msg_id": k,
                "retries": v["retries"],
                "confirmed": v["confirmed"],
                "processed": v["processed"],
                "age_seconds": time.time() - v["last_delivery"],
            }
            for k, v in self._pending.items()
        ]


class AgentStatus(Enum):
    """Agent 状态枚举"""
    ONLINE = "online"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass
class ConnectionInfo:
    """连接池中的单个连接"""
    agent_id: str
    channel: str          # 渠道标识: "main" / "script" / "health" / "mobile" / "legacy"
    ws: Any               # 底层 WS 连接
    is_handler: bool      # 是否当前 handler
    connected_at: float   # 连接建立时间
    last_ping: float      # 最后心跳时间
    role: str             # "primary" | "secondary"（同 channel 内的优先级）
    metadata: dict        # 额外信息（客户端版本、设备类型等）
    
    # P4 状态管理扩展
    status: str = "online"           # Agent 状态: online/busy/offline/error
    last_heartbeat: float = 0.0      # 最后心跳时间
    load: dict = field(default_factory=dict)  # 负载信息
    
    # 断连窗口
    is_disconnecting: bool = False   # 是否正在断开
    disconnect_time: float = 0.0     # 断开时间
    
    def update_heartbeat(self, status: str = None, load: dict = None):
        """更新心跳信息"""
        self.last_heartbeat = time.time()
        if status:
            self.status = status
        if load:
            self.load = load
    
    def mark_disconnecting(self):
        """标记为正在断开"""
        self.is_disconnecting = True
        self.disconnect_time = time.time()
    
    def is_replaced(self) -> bool:
        """检查是否已被替换（优雅窗口期内重连）"""
        return self.is_disconnecting and (time.time() - self.disconnect_time < HANDLER_GRACE_WINDOW)


# ==================== ReloadableConnectionPool 常量 ====================

# 最大同时 drain 的老池数量
MAX_PENDING_DRAINS = 2

# 老池 drain 超时时间（秒）
DRAIN_TIMEOUT = 60

# 最小 reload 间隔（秒）
MIN_RELOAD_INTERVAL = 30


# Channel 白名单
CHANNEL_WHITELIST = ["main", "script", "health", "web", "mobile", "legacy", "observer"]

# Handler 选举优先级（只有 main 可以当 handler）
HANDLER_CHANNEL_PRIORITY = {
    "main": 0,    # 最高 — AI 主框架
    "web": 1,     # Web 管理端
    "mobile": 2,  # 移动端
    "custom": 3,  # 自定义
    "script": 4,  # 脚本工具（不能当 handler）
    "health": 5,  # 健康检查（不能当 handler）
    "legacy": 9,  # 旧版兼容（不能当 handler）
}

# 可以当 handler 的 channel
HANDLER_ALLOWED_CHANNELS = ["main"]

# Handler 选举优雅窗口（秒）
# 断连后在窗口期内同 channel 重连，视为替换而非断线再上线
# term 不变；窗口期过后重连 term +1
HANDLER_GRACE_WINDOW = 8


class ConnectionPool:
    """连接池 — map-of-list 结构
    
    支持：
    1. 多连接共存 — 同 agent_id 可通过不同 channel 同时在线
    2. Handler 选举 — 只有 main channel 可以当 handler，带 term 机制
    3. 优雅降级 — handler 断连后自动提升备选，4s 窗口内重连 term 不变
    4. 向后兼容 — 旧客户端自动归入 legacy channel
    
    Term 机制：
    - handler 断连后，4s 窗口内同 channel 重连 → term 不变（视为替换）
    - 窗口期后重连或新连接 → term +1（视为新任期）
    - 高 term 的连接优先当选 handler
    - handler 断连时，先看同一 channel 是否有同 term 的备选替上
    """
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        
        # 连接池: {agent_id: {channel: [ConnectionInfo, ...]}}
        self._connections: Dict[str, Dict[str, List[ConnectionInfo]]] = {}
        
        # Handler 缓存: {agent_id: ConnectionInfo}
        self._handlers: Dict[str, ConnectionInfo] = {}
        
        # Agent 当前 term 号: {agent_id: term}
        self._terms: Dict[str, int] = {}
        
        # 配置参数
        self.max_connections_per_agent = self.config.get("max_connections_per_agent", 20)
        self.max_connections_per_channel = self.config.get("max_connections_per_channel", 5)
        self.grace_period = self.config.get("grace_period", 15)  # 连接断开优雅窗口 15s
        
        # AgentStateManager 引用（由 node.py 注入，用于状态统一）
        self._state_manager = None
        
        # 断连回调（由 node.py 注入，当 Agent 所有连接断开时触发）
        self._disconnect_callback = None
        
        logger.info("ConnectionPool 初始化完成 (grace_period=%ss, term_grace=%ss)", 
                     self.grace_period, HANDLER_GRACE_WINDOW)
    
    def set_state_manager(self, state_manager):
        """注入 AgentStateManager 引用，用于统一状态查询"""
        self._state_manager = state_manager
    
    def set_disconnect_callback(self, callback):
        """注入断连回调，当 Agent 所有连接断开时触发"""
        self._disconnect_callback = callback
    
    def register(self, agent_id: str, channel: str, ws: Any, 
                 role: str = "primary", metadata: dict = None) -> ConnectionInfo:
        """注册新连接
        
        Args:
            agent_id: Agent ID
            channel: 渠道标识
            ws: WebSocket 连接
            role: 角色 (primary/secondary)
            metadata: 额外信息（可含 term）
            
        Returns:
            ConnectionInfo 对象
        """
        # 白名单检查
        if channel not in CHANNEL_WHITELIST:
            channel = "custom"
        
        # 连接数限制（按 channel 独立计数，不互相争抢）
        if agent_id in self._connections:
            channel_count = len(self._connections[agent_id].get(channel, []))
            if channel_count >= self.max_connections_per_channel:
                logger.warning(f"[{agent_id}:{channel}] 连接数已达上限 ({self.max_connections_per_channel})")
                # 移除该 channel 最旧的连接
                self._remove_oldest_connection(agent_id, channel)
        
        # 确定 term
        # 如果在 HANDLER_GRACE_WINDOW 内有同 channel 的连接刚断开，维持当前 term
        # 否则 term+1（新连接/新任期）
        if self._is_within_grace(agent_id, channel):
            term = self._terms.get(agent_id, 1)
            logger.info(f"[{agent_id}:{channel}] 窗口期内重连，维持 term={term}")
        else:
            current_term = self._terms.get(agent_id, 0)
            term = current_term + 1
            self._terms[agent_id] = term
        
        # 创建连接信息
        conn = ConnectionInfo(
            agent_id=agent_id,
            channel=channel,
            ws=ws,
            is_handler=False,  # 稍后选举
            connected_at=time.time(),
            last_ping=time.time(),
            role=role,
            metadata={**(metadata or {}), "term": term},
        )
        
        # 添加到连接池
        if agent_id not in self._connections:
            self._connections[agent_id] = {}
        if channel not in self._connections[agent_id]:
            self._connections[agent_id][channel] = []
        
        self._connections[agent_id][channel].append(conn)
        
        logger.info(f"[{agent_id}:{channel}] 新连接接入 (role={role})")
        
        # 触发 handler 选举
        self._elect_handler(agent_id)
        
        return conn
    
    def unregister(self, agent_id: str, ws: Any):
        """注销连接（标记为断开）"""
        if agent_id not in self._connections:
            return
        
        for channel, conns in self._connections[agent_id].items():
            for conn in conns:
                if conn.ws == ws and not conn.is_disconnecting:
                    conn.mark_disconnecting()
                    
                    # Observer 连接立即清理，不做优雅等待
                    if conn.metadata.get("is_observer"):
                        self._remove_connection(agent_id, conn)
                        logger.info(f"[{agent_id}:{channel}] Observer 连接立即清理")
                        return
                    
                    logger.info(f"[{agent_id}:{channel}] 连接断开 (grace_period={self.grace_period}s)")
                    
                    # 如果是 handler 断开，触发重新选举
                    if conn.is_handler:
                        conn.is_handler = False
                        self._elect_handler(agent_id)
                    asyncio.create_task(self._grace_cleanup(agent_id, conn))
                    return

    def disconnect_agent(self, agent_id: str):
        """强制断开 Agent 的所有连接（用于心跳超时清理）"""
        if agent_id not in self._connections:
            return
        for channel, conns in list(self._connections[agent_id].items()):
            for conn in list(conns):
                if not conn.is_disconnecting:
                    conn.mark_disconnecting()
                    if conn.is_handler:
                        conn.is_handler = False
                    self._remove_connection(agent_id, conn)
        logger.info(f"[{agent_id}] 所有连接已强制断开 (heartbeat_timeout)")

    async def _grace_cleanup(self, agent_id: str, conn: ConnectionInfo):
        """优雅窗口清理"""
        await asyncio.sleep(self.grace_period)
        
        # Observer 连接不做超时清理，保持长连接
        if conn.metadata.get("is_observer"):
            return
        
        if conn.is_disconnecting and not conn.is_replaced():
            # 超时，清理连接
            self._remove_connection(agent_id, conn)
            logger.info(f"[{agent_id}:{conn.channel}] 优雅窗口超时，清理连接")
    
    def _remove_connection(self, agent_id: str, conn: ConnectionInfo):
        """移除连接"""
        if agent_id not in self._connections:
            return
        
        for channel, conns in self._connections[agent_id].items():
            if conn in conns:
                conns.remove(conn)
                logger.info(f"[{agent_id}:{channel}] 连接已移除")
                
                # 清理空映射
                if not conns:
                    del self._connections[agent_id][channel]
                if not self._connections[agent_id]:
                    del self._connections[agent_id]
                    # Agent 所有连接都已断开，触发断连回调
                    if self._disconnect_callback:
                        self._disconnect_callback(agent_id)
                
                # 重新选举 handler
                if conn.is_handler:
                    self._elect_handler(agent_id)
                return
    
    def _remove_oldest_connection(self, agent_id: str, channel: str = None):
        """移除指定 channel 最旧的连接"""
        if agent_id not in self._connections:
            return
        
        candidates = []
        if channel:
            # 只在该 channel 内找
            for conn in self._connections[agent_id].get(channel, []):
                if not conn.is_disconnecting:
                    candidates.append(conn)
        else:
            # 所有 channel
            for ch, conns in self._connections[agent_id].items():
                for conn in conns:
                    if not conn.is_disconnecting:
                        candidates.append(conn)
        
        if not candidates:
            return
        
        # 选最早连接的
        oldest = min(candidates, key=lambda c: c.connected_at)
        oldest.mark_disconnecting()
        asyncio.create_task(self._grace_cleanup(agent_id, oldest))
    
    def _is_within_grace(self, agent_id: str, channel: str) -> bool:
        """检查是否在 handler 选举优雅窗口期
        
        如果指定 channel 有连接刚刚断开（在 HANDLER_GRACE_WINDOW 内），
        返回 True — 应维持当前 term 不变（视为连接替换）
        """
        if agent_id not in self._connections:
            return False
        
        now = time.time()
        for ch, conns in self._connections[agent_id].items():
            if ch != channel:
                continue
            for conn in conns:
                if conn.is_disconnecting and conn.disconnect_time > 0:
                    if now - conn.disconnect_time < HANDLER_GRACE_WINDOW:
                        return True
        return False
    
    def _elect_handler(self, agent_id: str):
        """选举 handler — 带 term 机制
        
        规则：
        1. 只有 main channel 可以当 handler
        2. 优先级：高 term > 低 term
        3. 同 term 内: primary > secondary
        4. 同优先级下: 最早建立的连接
        
        Term 语义：
        - 每次新连接（非窗口期重连）term +1
        - handler 断连 → 4s 窗口期内同 channel 连接 term 维持不变
        - 高 term 表示较新的连接，优先当选
        """
        if agent_id not in self._connections:
            return
        
        candidates = []
        for channel, conns in self._connections[agent_id].items():
            # 只有允许的 channel 才能当 handler
            if channel not in HANDLER_ALLOWED_CHANNELS:
                continue
            
            for conn in conns:
                if conn.is_disconnecting:
                    continue
                # 从 metadata 获取 term，默认为 1
                conn_term = conn.metadata.get("term", 1) if conn.metadata else 1
                candidates.append((channel, -conn_term, conn.role == "primary", conn.connected_at, conn))
        
        if not candidates:
            # 没有可用的 handler
            if agent_id in self._handlers:
                old_handler = self._handlers[agent_id]
                old_handler.is_handler = False
                del self._handlers[agent_id]
            return
        
        # 排序: channel 优先级 > 高 term(-) > primary > 最早连接
        candidates.sort(key=lambda x: (
            HANDLER_CHANNEL_PRIORITY.get(x[0], 99),
            x[1],  # -term，高 term 优先
            not x[2],
            x[3]
        ))
        
        new_handler = candidates[0][4]
        old_handler = self._handlers.get(agent_id)
        
        if old_handler != new_handler:
            # 更新 handler
            if old_handler:
                old_handler.is_handler = False
            new_handler.is_handler = True
            self._handlers[agent_id] = new_handler
            
            # 获取新 handler 的 term
            new_term = new_handler.metadata.get("term", 1) if new_handler.metadata else 1
            logger.info(f"[{agent_id}] handler 选举: {new_handler.channel} (term={new_term}, role={new_handler.role})")
    
    def get_handler(self, agent_id: str) -> Optional[ConnectionInfo]:
        """获取 handler"""
        return self._handlers.get(agent_id)
    
    def get_all_connections(self, agent_id: str) -> List[ConnectionInfo]:
        """获取所有连接"""
        if agent_id not in self._connections:
            return []
        
        result = []
        for conns in self._connections[agent_id].values():
            for conn in conns:
                if not conn.is_disconnecting:
                    result.append(conn)
        return result
    
    def get_delivery_targets(self, agent_id: str, msg_type: str) -> List[Any]:
        """根据消息类型获取投递目标
        
        规则：
        - chat_message → 仅 handler
        - status_update/system_event/presence → 所有连接
        - ack → 发送方连接（由调用方指定）
        """
        if agent_id not in self._connections:
            return []
        
        # chat_message 只投递给 handler
        if msg_type in ("chat_message", "handler_only"):
            handler = self._handlers.get(agent_id)
            if handler and not handler.is_disconnecting:
                return [handler.ws]
            logger.debug(f"get_delivery_targets({agent_id}, {msg_type}): 无可用 handler")
            return []
        
        # 其他消息广播所有连接
        targets = []
        for channel, conns in self._connections[agent_id].items():
            for conn in conns:
                if not conn.is_disconnecting:
                    targets.append(conn.ws)
        return targets
    
    def update_heartbeat(self, agent_id: str, ws: Any, status: str = None, load: dict = None):
        """更新心跳"""
        if agent_id not in self._connections:
            return
        
        for channel, conns in self._connections[agent_id].items():
            for conn in conns:
                if conn.ws == ws:
                    conn.update_heartbeat(status, load)
                    return
    
    def get_status(self, agent_id: str) -> dict:
        """获取 Agent 统一状态
        
        使用 AgentStateManager 作为主数据源（status/heartbeat/load），
        用连接池补充连接数/handler/channels 信息。
        """
        # 优先从 AgentStateManager 取状态
        state = None
        if self._state_manager:
            state = self._state_manager.get(agent_id)
        
        if agent_id not in self._connections:
            if state:
                return {
                    "status": state.status,
                    "last_heartbeat": state.last_heartbeat,
                    "load": state.load,
                    "connections": 0,
                    "handler": None,
                    "handler_term": None,
                    "term": self._terms.get(agent_id, 1),
                    "channels": [],
                }
            return {"status": "offline", "connections": 0}
        
        connections = self.get_all_connections(agent_id)
        if not connections:
            if state:
                return {
                    "status": state.status,
                    "last_heartbeat": state.last_heartbeat,
                    "load": state.load,
                    "connections": 0,
                    "handler": None,
                    "handler_term": None,
                    "term": self._terms.get(agent_id, 1),
                    "channels": [],
                }
            return {"status": "offline", "connections": 0}
        
        handler = self._handlers.get(agent_id)
        base = {
            "connections": len(connections),
            "handler": handler.channel if handler else None,
            "handler_term": handler.metadata.get("term", 1) if handler and handler.metadata else None,
            "term": self._terms.get(agent_id, 1),
            "channels": [conn.channel for conn in connections],
        }
        if state:
            base["status"] = state.status
            base["last_heartbeat"] = state.last_heartbeat
            base["load"] = state.load
        else:
            base["status"] = connections[0].status
            base["last_heartbeat"] = connections[0].last_heartbeat
            base["load"] = connections[0].load
        return base
    
    def get_pool_summary(self) -> dict:
        """获取连接池摘要"""
        total_agents = len(self._connections)
        total_connections = sum(
            sum(len(conns) for conns in channels.values())
            for channels in self._connections.values()
        )
        return {
            "total_agents": total_agents,
            "total_connections": total_connections,
            "agents": {
                agent_id: self.get_status(agent_id)
                for agent_id in self._connections
            }
        }


class ReloadableConnectionPool(ConnectionPool):
    """可重载连接池 — 支持连接池动态刷新
    
    继承 ConnectionPool，增加：
    - Generation 计数器：每次 reload 递增，防状态污染
    - 新老池共存：老池 graceful drain，不强制断活跃连接
    - 防叠加：MAX_PENDING_DRAINS=2，超限跳过 reload
    - 防频繁：MIN_RELOAD_INTERVAL=30s，同 interval 内跳过
    - 超时清理：DRAIN_TIMEOUT=60s，超时强制切断僵尸连接
    
    使用方式：
    ```python
    pool = ReloadableConnectionPool(config)
    # ... 注册连接 ...
    success = pool.reload(reason="config_change")
    ```
    """
    
    def __init__(self, config: dict = None):
        super().__init__(config)
        
        # Generation 计数器
        self._generation: int = 0
        
        # 老池引用: {generation: ConnectionPool}
        self._old_pools: Dict[int, ConnectionPool] = {}
        
        # drain 任务: {generation: asyncio.Task}
        self._drain_tasks: Dict[int, asyncio.Task] = {}
        
        # 上次 reload 时间
        self._last_reload_time: float = 0.0
        
        logger.info("ReloadableConnectionPool 初始化完成 (gen=%d)", self._generation)
    
    @property
    def generation(self) -> int:
        """当前 generation"""
        return self._generation
    
    def reload(self, reason: str = "manual") -> bool:
        """触发连接池 reload
        
        Args:
            reason: reload 原因（用于日志和推送）
        
        Returns:
            True 表示成功触发，False 表示被跳过
        """
        now = time.time()
        
        # 防频繁触发
        if now - self._last_reload_time < MIN_RELOAD_INTERVAL:
            logger.warning(
                f"reload 过于频繁（间隔 {now - self._last_reload_time:.1f}s < {MIN_RELOAD_INTERVAL}s），跳过 "
                f"(reason={reason})"
            )
            return False
        
        # 清理已完成 drain 的老池
        self._clean_completed_drains()
        
        # 检查叠加上限
        active_drains = sum(1 for p in self._old_pools.values() if not self._is_pool_drained(p))
        if active_drains >= MAX_PENDING_DRAINS:
            logger.warning(
                f"已达最大待 drain 池数 ({MAX_PENDING_DRAINS})，跳过 reload "
                f"(reason={reason})"
            )
            return False
        
        # 递增 generation
        new_gen = self._generation + 1
        logger.info(f"reload 触发: gen {self._generation} -> {new_gen} (reason={reason})")
        
        # 将当前连接池状态保存为老池
        old_pool = self._create_snapshot_pool()
        if old_pool:
            self._old_pools[self._generation] = old_pool
            # 启动 drain 任务
            self._drain_tasks[self._generation] = asyncio.create_task(
                self._drain_old_pool(self._generation, old_pool)
            )
        
        # 更新 generation
        self._generation = new_gen
        self._last_reload_time = now
        
        logger.info(f"reload 完成: 新 gen={new_gen}, 老池数={len(self._old_pools)}")
        return True
    
    def _create_snapshot_pool(self) -> Optional[ConnectionPool]:
        """创建当前连接池的快照（用于 drain）"""
        # 获取当前所有连接
        snapshot = ConnectionPool(self.config)
        has_connections = False
        
        for agent_id, channels in self._connections.items():
            for channel, conns in channels.items():
                for conn in conns:
                    if not conn.is_disconnecting:
                        # 注册到快照池
                        snapshot._connections.setdefault(agent_id, {}).setdefault(channel, []).append(conn)
                        has_connections = True
        
        return snapshot if has_connections else None
    
    async def _drain_old_pool(self, gen: int, pool: ConnectionPool):
        """drain 老池 — 等待连接自然释放，超时强制切断"""
        logger.info(f"[gen={gen}] 开始 drain 老池")
        
        start_time = time.time()
        
        while True:
            # 检查是否所有连接已释放
            if self._is_pool_drained(pool):
                logger.info(f"[gen={gen}] 老池 drain 完成（所有连接已释放）")
                break
            
            # 检查超时
            elapsed = time.time() - start_time
            if elapsed >= DRAIN_TIMEOUT:
                logger.warning(f"[gen={gen}] drain 超时 ({DRAIN_TIMEOUT}s)，强制切断僵尸连接")
                self._force_drain_pool(pool)
                break
            
            # 每 5s 检查一次
            await asyncio.sleep(5)
        
        # 清理
        self._old_pools.pop(gen, None)
        self._drain_tasks.pop(gen, None)
        logger.info(f"[gen={gen}] 老池已清理")
    
    def _is_pool_drained(self, pool: ConnectionPool) -> bool:
        """检查池是否已 drain（无活跃连接）"""
        for agent_id, channels in pool._connections.items():
            for channel, conns in channels.items():
                for conn in conns:
                    if not conn.is_disconnecting:
                        return False
        return True
    
    def _force_drain_pool(self, pool: ConnectionPool):
        """强制切断池中所有连接"""
        for agent_id, channels in pool._connections.items():
            for channel, conns in channels.items():
                for conn in conns:
                    if not conn.is_disconnecting:
                        conn.mark_disconnecting()
                        logger.info(f"[force_drain] 强制切断 {agent_id}:{channel}")
    
    def _clean_completed_drains(self):
        """清理已完成 drain 的老池"""
        completed = []
        for gen, pool in self._old_pools.items():
            if self._is_pool_drained(pool):
                completed.append(gen)
        
        for gen in completed:
            self._old_pools.pop(gen, None)
            task = self._drain_tasks.pop(gen, None)
            if task and not task.done():
                task.cancel()
            logger.info(f"清理已完成 drain 的老池 gen={gen}")
    
    def get_reload_status(self) -> dict:
        """获取 reload 状态信息"""
        self._clean_completed_drains()
        
        return {
            "generation": self._generation,
            "last_reload_time": self._last_reload_time,
            "old_pools_count": len(self._old_pools),
            "active_drains": sum(1 for p in self._old_pools.values() if not self._is_pool_drained(p)),
            "drain_tasks": {
                gen: {
                    "done": task.done(),
                    "cancelled": task.cancelled() if task.done() else False,
                }
                for gen, task in self._drain_tasks.items()
            },
        }
