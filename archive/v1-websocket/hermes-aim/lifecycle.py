#!/usr/bin/env python3
"""
AIM P4 Agent 生命周期管理模块 — 客户端侧
版本：v0.2 (P2.3 合并)

注意：此模块是客户端侧的心跳发送器 + 事件钩子系统。
与服务端侧的 AgentStateManager (registry.py) 互补：
  - AgentStateManager: 接收心跳、检测超时、广播事件
  - AgentLifecycle: 发送心跳、注册钩子

P4 状态管理的 AgentStatus 枚举由 connection_pool.py 统一提供。
"""

import asyncio
import json
import time
import logging
from enum import Enum
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict

# 导入连接池
from connection_pool import ConnectionPool, ConnectionInfo, AgentStatus

logger = logging.getLogger(__name__)


# P4 状态管理已集成到 ConnectionPool
# AgentStatus 枚举和状态管理现在在 connection_pool.py 中
# 这里保留向后兼容的导入


class LifecycleEvent(Enum):
    """生命周期事件类型"""
    AGENT_ONLINE = "agent_online"
    AGENT_OFFLINE = "agent_offline"
    AGENT_STATUS_CHANGE = "agent_status_change"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    DEREGISTER = "deregister"


@dataclass
class AgentLoad:
    """Agent 负载信息（精简版：只保留 pending_tasks）"""
    pending_tasks: int = 0
    
    def to_dict(self) -> dict:
        return {
            "pending_tasks": self.pending_tasks
        }


@dataclass
class HeartbeatMessage:
    """心跳消息"""
    agent_id: str
    status: str
    timestamp: float
    load: Optional[AgentLoad] = None
    
    def to_json(self) -> str:
        data = {
            "cmd": "heartbeat",
            "agent_id": self.agent_id,
            "status": self.status,
            "timestamp": self.timestamp
        }
        if self.load:
            data["load"] = self.load
        return json.dumps(data)


@dataclass
class LifecycleMessage:
    """生命周期事件消息"""
    event: str
    agent_id: str
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    reason: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    
    def to_json(self) -> str:
        data = {
            "cmd": "agent_lifecycle",
            "event": self.event,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp
        }
        if self.old_status:
            data["old_status"] = self.old_status
        if self.new_status:
            data["new_status"] = self.new_status
        if self.reason:
            data["reason"] = self.reason
        return json.dumps(data)


class AgentLifecycle:
    """
    Agent 生命周期管理器
    
    V2 版本：集成 ConnectionPool，复用连接池的心跳机制
    
    功能：
    1. 心跳保活：通过连接池的心跳机制
    2. 状态管理：维护 Agent 状态（online/busy/offline/error）
    3. 事件钩子：响应其他 Agent 的生命周期事件
    4. 优雅退出：主动发送 deregister 通知
    """
    
    def __init__(self, agent_id: str, server_url: str, config: Dict[str, Any] = None):
        """
        初始化生命周期管理器
        
        Args:
            agent_id: Agent ID
            server_url: AIM Server WebSocket URL
            config: 配置参数
        """
        self.agent_id = agent_id
        self.server_url = server_url
        self.config = config or {}
        
        # 配置参数
        self.heartbeat_interval = self.config.get("heartbeat_interval", 30)
        self.heartbeat_timeout = self.config.get("heartbeat_timeout", 90)
        self.max_missed_heartbeats = self.config.get("max_missed_heartbeats", 3)
        self.auto_reconnect = self.config.get("auto_reconnect", True)
        self.status_report = self.config.get("status_report", True)
        self.graceful_shutdown_timeout = self.config.get("graceful_shutdown_timeout", 15)
        
        # 重连配置
        self.reconnect_backoff_cap = self.config.get("reconnect_backoff_cap", 30)  # 单次退避上限 30s
        self.reconnect_total_timeout = self.config.get("reconnect_total_timeout", 120)  # 总重连超时 120s
        
        # 心跳追踪
        self._missed_heartbeats: int = 0
        
        # 状态
        self.status = AgentStatus.OFFLINE
        self.connected_at: Optional[float] = None
        self.last_heartbeat: Optional[float] = None
        
        # 负载信息
        self.load = {}
        
        # 事件钩子
        self._hooks: Dict[str, List[Callable]] = defaultdict(list)
        
        # 内部任务
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._ws_connection = None
        
        # 连接池引用（由外部注入）
        self._connection_pool: Optional[ConnectionPool] = None
        
        logger.info(f"AgentLifecycle 初始化: {agent_id} -> {server_url}")
    
    def set_connection_pool(self, pool: ConnectionPool):
        """注入连接池"""
        self._connection_pool = pool
    
    def on(self, event: str):
        """
        事件钩子装饰器
        
        Usage:
            @lifecycle.on("agent_online")
            async def on_online(event):
                print(f"Agent {event['agent_id']} 上线了")
        """
        def decorator(func: Callable):
            self._hooks[event].append(func)
            return func
        return decorator
    
    def add_hook(self, event: str, func: Callable):
        """添加事件钩子"""
        self._hooks[event].append(func)
    
    async def start(self) -> bool:
        """
        启动生命周期管理
        
        Returns:
            bool: 是否启动成功
        """
        try:
            # 连接 AIM Server
            if not await self._connect():
                logger.error("无法连接到 AIM Server")
                return False
            
            # 更新状态
            self.status = AgentStatus.ONLINE
            self.connected_at = time.time()
            
            # 发送注册/上线通知
            await self._send_register()
            
            # 启动心跳循环
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            logger.info(f"生命周期管理启动成功: {self.agent_id}")
            return True
            
        except Exception as e:
            logger.error(f"启动生命周期管理失败: {e}")
            self.status = AgentStatus.ERROR
            return False
    
    async def stop(self):
        """
        优雅停止生命周期管理（可配置超时，默认 15s）
        """
        logger.info(f"停止生命周期管理: {self.agent_id} (graceful timeout={self.graceful_shutdown_timeout}s)")
        
        try:
            # 发送下线通知（带超时保护）
            await asyncio.wait_for(
                self._send_deregister(),
                timeout=self.graceful_shutdown_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"下线通知发送超时 ({self.graceful_shutdown_timeout}s)，强制继续")
        except Exception as e:
            logger.warning(f"下线通知发送失败: {e}")
        
        # 更新状态
        self.status = AgentStatus.OFFLINE
        
        # 停止心跳
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await asyncio.wait_for(
                    self._heartbeat_task,
                    timeout=self.graceful_shutdown_timeout
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        # 断开连接
        await self._disconnect()
        
        logger.info(f"生命周期管理已停止: {self.agent_id}")
    
    async def set_status(self, new_status: AgentStatus, reason: str = None):
        """
        设置 Agent 状态
        
        Args:
            new_status: 新状态
            reason: 状态变更原因
        """
        if self.status == new_status:
            return
        
        old_status = self.status
        self.status = new_status
        
        logger.info(f"状态变更: {old_status.value} -> {new_status.value} (原因: {reason})")
        
        # 发送状态变更通知
        if self.status_report:
            await self._send_status_change(old_status, new_status, reason)
        
        # 触发本地钩子
        event = {
            "type": "status_change",
            "agent_id": self.agent_id,
            "old_status": old_status.value,
            "new_status": new_status.value,
            "reason": reason,
            "timestamp": time.time()
        }
        await self._trigger_hooks("status_change", event)
    
    def update_load(self, pending_tasks: int = None):
        """
        更新负载信息
        
        Args:
            pending_tasks: 待处理任务数
        """
        if pending_tasks is not None:
            self.load["pending_tasks"] = pending_tasks
    
    def get_status(self) -> Dict[str, Any]:
        """
        获取当前状态信息
        
        Returns:
            dict: 状态信息
        """
        return {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "connected_at": self.connected_at,
            "last_heartbeat": self.last_heartbeat,
            "uptime": time.time() - self.connected_at if self.connected_at else 0,
            "load": self.load if isinstance(self.load, dict) else {}
        }
    
    async def _connect(self) -> bool:
        """连接到 AIM Server"""
        try:
            import websockets
            self._ws_connection = await websockets.connect(self.server_url)
            logger.info(f"已连接到 AIM Server: {self.server_url}")
            return True
        except ImportError:
            logger.error("需要安装 websockets: pip install websockets")
            return False
        except Exception as e:
            logger.error(f"连接 AIM Server 失败: {e}")
            return False
    
    async def _disconnect(self):
        """断开连接"""
        if self._ws_connection:
            await self._ws_connection.close()
            self._ws_connection = None
    
    async def _send_register(self):
        """发送注册/上线消息（标准化 WS 命令格式）"""
        message = {
            "cmd": "register",
            "agent_id": self.agent_id,
            "status": self.status.value,
            "timestamp": time.time()
        }
        await self._send_message(message)
        logger.info(f"已发送注册消息: {self.agent_id}")
    
    async def _send_deregister(self, reason: str = "shutdown"):
        """发送下线消息
        
        Args:
            reason: 下线原因 (shutdown/manual_disconnect/timeout)
        """
        message = LifecycleMessage(
            event=LifecycleEvent.DEREGISTER.value,
            agent_id=self.agent_id,
            reason=reason,
            timestamp=time.time()
        )
        await self._send_message(json.loads(message.to_json()))
        logger.info(f"已发送下线消息: {self.agent_id} (reason={reason})")
    
    async def _send_status_change(self, old_status: AgentStatus, new_status: AgentStatus, reason: str = None):
        """发送状态变更消息"""
        message = LifecycleMessage(
            event=LifecycleEvent.AGENT_STATUS_CHANGE.value,
            agent_id=self.agent_id,
            old_status=old_status.value,
            new_status=new_status.value,
            reason=reason,
            timestamp=time.time()
        )
        await self._send_message(json.loads(message.to_json()))
    
    async def _send_heartbeat(self):
        """发送心跳"""
        message = HeartbeatMessage(
            agent_id=self.agent_id,
            status=self.status.value,
            timestamp=time.time(),
            load=self.load
        )
        await self._send_message(json.loads(message.to_json()))
        self.last_heartbeat = time.time()
        
        # 更新连接池心跳
        if self._connection_pool and self._ws_connection:
            self._connection_pool.update_heartbeat(
                self.agent_id, 
                self._ws_connection, 
                self.status.value, 
                self.load
            )
        
        logger.debug(f"心跳已发送: {self.agent_id}")
    
    async def _send_message(self, message: dict):
        """发送 WebSocket 消息"""
        if self._ws_connection:
            try:
                await self._ws_connection.send(json.dumps(message))
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
                if self.auto_reconnect:
                    await self._reconnect()
    
    async def _reconnect(self):
        """重新连接（指数退避，30s 上限，120s 总超时）"""
        logger.info("尝试重新连接...")
        await self._disconnect()
        
        base_delay = 1  # 基础延迟 1s
        total_elapsed = 0
        attempt = 0
        
        while total_elapsed < self.reconnect_total_timeout:
            attempt += 1
            delay = min(base_delay * (2 ** (attempt - 1)), self.reconnect_backoff_cap)
            
            # 不超过总超时
            remaining = self.reconnect_total_timeout - total_elapsed
            if remaining <= 0:
                break
            delay = min(delay, remaining)
            
            logger.info(f"重连尝试 {attempt}，等待 {delay}s (已用 {total_elapsed:.0f}/{self.reconnect_total_timeout}s)...")
            await asyncio.sleep(delay)
            total_elapsed += delay
            
            if await self._connect():
                await self._send_register()
                self._missed_heartbeats = 0
                logger.info(f"重新连接成功 (耗时 {total_elapsed:.0f}s)")
                return
        
        logger.error(f"重新连接失败（总耗时 {total_elapsed:.0f}s 超限），标记为 ERROR")
        self.status = AgentStatus.ERROR
    
    async def _heartbeat_loop(self):
        """心跳循环（带 missed heartbeats 追踪）"""
        logger.info(f"心跳循环启动: 间隔 {self.heartbeat_interval}s, 超时 {self.heartbeat_timeout}s, 最大丢失 {self.max_missed_heartbeats}次")
        while True:
            try:
                await self._send_heartbeat()
                self._missed_heartbeats = 0  # 发送成功则重置
                await asyncio.sleep(self.heartbeat_interval)
                
                # 检测是否超过心跳超时（被动检测，结合 server 端 ack）
                if self.last_heartbeat:
                    elapsed = time.time() - self.last_heartbeat
                    if elapsed > self.heartbeat_timeout:
                        self._missed_heartbeats += 1
                        logger.warning(f"心跳超时: {elapsed:.0f}s > {self.heartbeat_timeout}s (连续 {self._missed_heartbeats}/{self.max_missed_heartbeats})")
                        
                        if self._missed_heartbeats >= self.max_missed_heartbeats:
                            logger.error(f"连续 {self.max_missed_heartbeats} 次心跳超时，触发重连")
                            self.status = AgentStatus.ERROR
                            await self._trigger_hooks("heartbeat_timeout", {
                                "agent_id": self.agent_id,
                                "missed": self._missed_heartbeats,
                                "timestamp": time.time()
                            })
                            if self.auto_reconnect:
                                await self._reconnect()
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳发送失败: {e}")
                await asyncio.sleep(1)
    
    async def _trigger_hooks(self, event: str, data: dict):
        """触发事件钩子"""
        for hook in self._hooks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(data)
                else:
                    hook(data)
            except Exception as e:
                logger.error(f"钩子执行失败: {event} - {e}")
    
    async def handle_message(self, message: dict):
        """
        处理从 Server 收到的消息（标准化 cmd 字段匹配）

        Args:
            message: 消息数据
        """
        msg_cmd = message.get("cmd", message.get("type", ""))
        
        if msg_cmd == "heartbeat_ack":
            # 心跳响应
            logger.debug("收到心跳响应")
            
        elif msg_cmd == "agent_lifecycle":
            # 生命周期事件
            event = message.get("event")
            agent_id = message.get("agent_id")
            
            logger.info(f"收到生命周期事件: {event} - {agent_id}")
            
            # 触发对应钩子
            await self._trigger_hooks(event, message)
            
            # 广播给所有钩子
            await self._trigger_hooks("lifecycle_event", message)
        
        elif msg_cmd == "agents_list":
            # 在线 Agent 列表更新
            agents = message.get("agents", [])
            logger.info(f"在线 Agent 列表: {agents}")
            await self._trigger_hooks("agents_update", {"agents": agents})


class LifecycleManager:
    """
    生命周期管理器工厂
    
    用于管理多个 Agent 的生命周期
    """
    
    def __init__(self):
        self._agents: Dict[str, AgentLifecycle] = {}
    
    def create(self, agent_id: str, server_url: str, config: Dict[str, Any] = None) -> AgentLifecycle:
        """创建 Agent 生命周期管理器"""
        lifecycle = AgentLifecycle(agent_id, server_url, config)
        self._agents[agent_id] = lifecycle
        return lifecycle
    
    def get(self, agent_id: str) -> Optional[AgentLifecycle]:
        """获取 Agent 生命周期管理器"""
        return self._agents.get(agent_id)
    
    async def stop_all(self):
        """停止所有 Agent"""
        for lifecycle in self._agents.values():
            await lifecycle.stop()
    
    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有 Agent 状态"""
        return {
            agent_id: lifecycle.get_status()
            for agent_id, lifecycle in self._agents.items()
        }


# 使用示例
async def example_usage():
    """使用示例"""
    
    # 创建生命周期管理器
    lifecycle = AgentLifecycle(
        agent_id="ZS0001",
        server_url="ws://127.0.0.1:18900",
        config={
            "heartbeat_interval": 30,
            "heartbeat_timeout": 90,
            "max_missed_heartbeats": 3,
            "auto_reconnect": True,
            "status_report": True,
            "graceful_shutdown_timeout": 15,
            "reconnect_backoff_cap": 30,
            "reconnect_total_timeout": 120
        }
    )
    
    # 注册钩子
    @lifecycle.on("agent_online")
    async def on_agent_online(event):
        print(f"🟢 Agent {event['agent_id']} 上线了")
    
    @lifecycle.on("agent_offline")
    async def on_agent_offline(event):
        print(f"🔴 Agent {event['agent_id']} 离线了: {event.get('reason', '未知原因')}")
    
    @lifecycle.on("agent_status_change")
    async def on_status_change(event):
        print(f"🔄 Agent {event['agent_id']} 状态变更: {event['old_status']} -> {event['new_status']}")
    
    @lifecycle.on("status_change")
    async def on_my_status_change(event):
        print(f"📢 我的状态变更: {event['old_status']} -> {event['new_status']}")
    
    # 启动
    if await lifecycle.start():
        print("✅ 生命周期管理启动成功")
        
        # 更新负载信息
        lifecycle.update_load(pending_tasks=2)
        
        # 设置为忙碌状态
        await lifecycle.set_status(AgentStatus.BUSY, reason="处理任务")
        
        # 模拟运行一段时间
        await asyncio.sleep(60)
        
        # 停止
        await lifecycle.stop()
        print("✅ 生命周期管理已停止")
    else:
        print("❌ 生命周期管理启动失败")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(example_usage())
