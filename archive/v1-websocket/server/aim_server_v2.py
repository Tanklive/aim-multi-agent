# AIM V2 Server - Multi-Connection WebSocket Manager
# Phase 1: Core framework
# Author: 呱呱 (ZS0001)
# Date: 2026-06-05

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set

import websockets
from websockets.server import serve

# ============================================================
# Constants
# ============================================================

MAX_PLATFORM_CONNECTIONS = 5  # 平台级连接上限
SYSTEM_CHANNELS = {"main", "script", "health"}  # 系统级 channel，不计入上限
ACK_TIMEOUT_SECONDS = 30  # 消息确认超时
MAX_RETRIES = 3  # 最大重试次数


# ============================================================
# Data Models
# ============================================================

class MsgState(Enum):
    """消息确认状态机"""
    PENDING = "pending"      # 已发送，等待 received
    SENT = "sent"            # 初始状态
    RECEIVED = "received"    # 收到 received ack
    ACKED = "acked"          # 收到最终 ack
    NACKED = "nacked"        # 收到 nack
    TIMEOUT = "timeout"      # 超时未确认
    FAILED = "failed"        # 最终失败


class ConnType(Enum):
    """连接类型"""
    SYSTEM = "system"    # main/script/health，不计入上限
    PLATFORM = "platform"  # web/mobile/qq/ext，计入上限


@dataclass
class Connection:
    """WebSocket 连接"""
    conn_id: str
    ws: websockets.WebSocketServerProtocol
    channel: str
    conn_type: ConnType
    agent_id: str
    auth_ts: float  # 认证时间戳，用于 tiebreaker
    term: int = 0  # 连接序号，用于 tiebreaker
    connected_at: float = field(default_factory=time.time)
    is_alive: bool = True


@dataclass
class PendingMessage:
    """待确认消息"""
    msg_id: str
    payload: dict
    target_agent: str
    target_channel: Optional[str]
    state: MsgState = MsgState.SENT
    send_ts: float = field(default_factory=time.time)
    last_ack_ts: Optional[float] = None
    retry_count: int = 0
    timeout_task: Optional[asyncio.Task] = field(default=None, repr=False)


# ============================================================
# Connection Manager
# ============================================================

class ConnectionManager:
    """多连接管理器"""

    def __init__(self):
        # agent_id -> {conn_id -> Connection}
        self._connections: Dict[str, Dict[str, Connection]] = {}
        # msg_id -> PendingMessage
        self._pending: Dict[str, PendingMessage] = {}
        # Lock for thread safety
        self._lock = asyncio.Lock()

    # ---------- Connection Management ----------

    async def register(self, ws, channel: str, agent_id: str, auth_ts: float) -> Optional[Connection]:
        """注册新连接"""
        async with self._lock:
            is_system = channel in SYSTEM_CHANNELS
            conn_type = ConnType.SYSTEM if is_system else ConnType.PLATFORM

            # 平台级连接计数检查
            if conn_type == ConnType.PLATFORM:
                count = self._count_platform(agent_id)
                if count >= MAX_PLATFORM_CONNECTIONS:
                    return None  # 拒绝连接

            # 计算 term（该 agent 在此 channel 上的连接序号）
            existing = self._connections.get(agent_id, {})
            same_channel = [c for c in existing.values() if c.channel == channel]
            term = len(same_channel)

            conn_id = str(uuid.uuid4())[:8]
            conn = Connection(
                conn_id=conn_id,
                ws=ws,
                channel=channel,
                conn_type=conn_type,
                agent_id=agent_id,
                auth_ts=auth_ts,
                term=term,
            )

            if agent_id not in self._connections:
                self._connections[agent_id] = {}
            self._connections[agent_id][conn_id] = conn

            return conn

    async def unregister(self, conn_id: str, agent_id: str):
        """注销连接"""
        async with self._lock:
            if agent_id in self._connections:
                self._connections[agent_id].pop(conn_id, None)
                if not self._connections[agent_id]:
                    del self._connections[agent_id]

    def _count_platform(self, agent_id: str) -> int:
        """统计平台级连接数"""
        conns = self._connections.get(agent_id, {})
        return sum(1 for c in conns.values() if c.conn_type == ConnType.PLATFORM)

    def get_connection_count(self, agent_id: str) -> dict:
        """获取连接统计"""
        conns = self._connections.get(agent_id, {})
        system = sum(1 for c in conns.values() if c.conn_type == ConnType.SYSTEM)
        platform = sum(1 for c in conns.values() if c.conn_type == ConnType.PLATFORM)
        return {"system": system, "platform": platform, "total": system + platform}

    # ---------- Tiebreaker: channel=main > term > auth_ts ----------

    def select_target(self, agent_id: str, channel: Optional[str] = None) -> Optional[Connection]:
        """选择目标连接（tiebreaker 逻辑）"""
        conns = self._connections.get(agent_id, {})
        alive = [c for c in conns.values() if c.is_alive]

        if not alive:
            return None

        # 指定 channel 时，优先选择
        if channel:
            candidates = [c for c in alive if c.channel == channel]
            if candidates:
                return self._tiebreak(candidates)
            # 指定 channel 无连接，fallback 到 main
            candidates = [c for c in alive if c.channel == "main"]
            if candidates:
                return self._tiebreak(candidates)

        # 默认选 main channel
        candidates = [c for c in alive if c.channel == "main"]
        if not candidates:
            candidates = alive

        return self._tiebreak(candidates)

    def _tiebreak(self, conns: list) -> Connection:
        """Tiebreaker: channel=main > term 大 > auth_ts 早"""
        return sorted(conns, key=lambda c: (
            0 if c.channel == "main" else 1,  # main 优先
            -c.term,  # term 大优先
            c.auth_ts,  # auth 早优先
        ))[0]

    # ---------- Message Acknowledgment State Machine ----------

    async def send_with_ack(self, target_agent: str, payload: dict,
                            target_channel: Optional[str] = None) -> str:
        """发送消息并进入确认状态机"""
        msg_id = str(uuid.uuid4())[:12]
        pending = PendingMessage(
            msg_id=msg_id,
            payload=payload,
            target_agent=target_agent,
            target_channel=target_channel,
        )
        self._pending[msg_id] = pending

        # 发送
        success = await self._deliver(msg_id)
        if success:
            # 启动超时监控
            pending.timeout_task = asyncio.create_task(
                self._timeout_watch(msg_id)
            )
        else:
            pending.state = MsgState.FAILED

        return msg_id

    async def handle_received(self, msg_id: str):
        """处理 received ack（中间确认，重置超时）"""
        pending = self._pending.get(msg_id)
        if not pending:
            return
        pending.state = MsgState.RECEIVED
        pending.last_ack_ts = time.time()
        pending.retry_count = 0  # 重置重试计数
        # 重置超时计时器
        if pending.timeout_task:
            pending.timeout_task.cancel()
        pending.timeout_task = asyncio.create_task(
            self._timeout_watch(msg_id)
        )

    async def handle_ack(self, msg_id: str):
        """处理最终 ack"""
        pending = self._pending.get(msg_id)
        if not pending:
            return
        pending.state = MsgState.ACKED
        pending.last_ack_ts = time.time()
        if pending.timeout_task:
            pending.timeout_task.cancel()
        # 清理
        self._pending.pop(msg_id, None)

    async def handle_nack(self, msg_id: str, reason: str = ""):
        """处理 nack（拒绝确认）"""
        pending = self._pending.get(msg_id)
        if not pending:
            return
        pending.state = MsgState.NACKED
        if pending.timeout_task:
            pending.timeout_task.cancel()
        # nack 后不重试，直接标记失败
        self._pending.pop(msg_id, None)

    async def _timeout_watch(self, msg_id: str):
        """超时监控：30s 内未收到 ack 则重试"""
        await asyncio.sleep(ACK_TIMEOUT_SECONDS)
        pending = self._pending.get(msg_id)
        if not pending or pending.state in (MsgState.ACKED, MsgState.NACKED):
            return

        if pending.retry_count >= MAX_RETRIES:
            pending.state = MsgState.FAILED
            self._pending.pop(msg_id, None)
            return

        pending.retry_count += 1
        pending.state = MsgState.TIMEOUT
        await self._deliver(msg_id)  # 重推
        pending.timeout_task = asyncio.create_task(
            self._timeout_watch(msg_id)
        )

    async def _deliver(self, msg_id: str) -> bool:
        """实际投递消息"""
        pending = self._pending.get(msg_id)
        if not pending:
            return False

        target = self.select_target(pending.target_agent, pending.target_channel)
        if not target or not target.is_alive:
            return False

        try:
            wire = json.dumps({
                "type": "message",
                "msg_id": msg_id,
                "from": "server",
                "payload": pending.payload,
            })
            await target.ws.send(wire)
            pending.state = MsgState.SENT
            return True
        except Exception:
            target.is_alive = False
            return False

    # ---------- Graceful Shutdown ----------

    async def disconnect(self, conn_id: str, agent_id: str, reason: str = "server_shutdown"):
        """主动断开连接（graceful）"""
        conns = self._connections.get(agent_id, {})
        conn = conns.get(conn_id)
        if not conn:
            return

        try:
            # 发送 disconnect 命令
            await conn.ws.send(json.dumps({
                "type": "disconnect",
                "reason": reason,
                "conn_id": conn_id,
            }))
            await conn.ws.close(1000, reason)
        except Exception:
            pass
        finally:
            await self.unregister(conn_id, agent_id)

    async def shutdown_all(self):
        """关闭所有连接（服务器停止时）"""
        tasks = []
        for agent_id, conns in self._connections.items():
            for conn_id in list(conns.keys()):
                tasks.append(self.disconnect(conn_id, agent_id, "server_shutdown"))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# ============================================================
# WebSocket Server
# ============================================================

class AIMServer:
    """AIM V2 WebSocket Server"""

    def __init__(self, host: str = "127.0.0.1", port: int = 18900):
        self.host = host
        self.port = port
        self.cm = ConnectionManager()

    async def handler(self, ws):
        """WebSocket 连接处理"""
        agent_id = None
        conn = None

        try:
            # 等待认证消息
            auth_msg = await asyncio.wait_for(ws.recv(), timeout=10)
            auth = json.loads(auth_msg)

            if auth.get("type") != "auth":
                await ws.close(4001, "Expected auth message")
                return

            agent_id = auth.get("agent_id")
            channel = auth.get("channel", "main")
            auth_ts = auth.get("auth_ts", time.time())

            if not agent_id:
                await ws.close(4002, "Missing agent_id")
                return

            conn = await self.cm.register(ws, channel, agent_id, auth_ts)
            if not conn:
                await ws.close(4003, "Platform connection limit reached")
                return

            # 发送认证成功
            await ws.send(json.dumps({
                "type": "auth_ok",
                "conn_id": conn.conn_id,
                "channel": channel,
                "term": conn.term,
            }))

            # 消息循环
            async for raw in ws:
                await self._handle_message(ws, raw, conn)

        except websockets.ConnectionClosed:
            pass
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"[AIMServer] Error: {e}")
        finally:
            if conn and agent_id:
                await self.cm.unregister(conn.conn_id, agent_id)

    async def _handle_message(self, ws, raw: str, conn: Connection):
        """处理客户端消息"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "received":
            # 中间确认
            msg_id = msg.get("msg_id")
            if msg_id:
                await self.cm.handle_received(msg_id)

        elif msg_type == "ack":
            # 最终确认
            msg_id = msg.get("msg_id")
            if msg_id:
                await self.cm.handle_ack(msg_id)

        elif msg_type == "nack":
            # 拒绝确认
            msg_id = msg.get("msg_id")
            reason = msg.get("reason", "")
            if msg_id:
                await self.cm.handle_nack(msg_id, reason)

        elif msg_type == "send":
            # 客户端发送消息到其他 agent
            target = msg.get("to")
            payload = msg.get("payload", {})
            if target:
                msg_id = await self.cm.send_with_ack(target, payload)
                await ws.send(json.dumps({
                    "type": "send_ok",
                    "msg_id": msg_id,
                }))

        elif msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))

    async def run(self):
        """启动服务器"""
        print(f"[AIMServer] Starting on ws://{self.host}:{self.port}")
        async with serve(self.handler, self.host, self.port):
            await asyncio.Future()  # 永久运行


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    server = AIMServer()
    asyncio.run(server.run())
