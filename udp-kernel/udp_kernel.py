#!/usr/bin/env python3
"""
AIM UDP 消息内核 - 零依赖，纯 Python 实现

功能：
  1. UDP 消息收发
  2. 消息路由 (DM / 群聊)
  3. 心跳保活
  4. 离线消息队列
  5. 消息去重

用法：
  python3 udp_kernel.py --agent-id ZS0003 --port 19003
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

# ============================================================
# 日志配置
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stderr)]
)
log = logging.getLogger("udp-kernel")


# ============================================================
# 常量定义
# ============================================================

PROTOCOL_VERSION = 1
MAX_MESSAGE_SIZE = 65536  # 64KB
BROADCAST_PORT = 19000
HEARTBEAT_INTERVAL = 30  # 秒
HEARTBEAT_TIMEOUT = 90   # 秒
MAX_OFFLINE_MESSAGES = 1000
OFFLINE_EXPIRE_DAYS = 7

# 默认 Agent 端口映射
DEFAULT_PORTS = {
    "ZS0001": 19001,
    "ZS0002": 19002,
    "ZS0003": 19003,
    "ZS0004": 19004,
    "ZS0005": 19005,
}


# ============================================================
# 数据结构
# ============================================================

@dataclass
class AIMMessage:
    """AIM 消息结构"""
    v: int = PROTOCOL_VERSION
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "dm"  # dm, group, ping, pong, join, leave, ack
    sender: str = ""
    receiver: str = ""  # Agent ID 或群组名
    content: str = ""
    ts: float = field(default_factory=time.time)
    reply_to: Optional[str] = None
    meta: Dict = field(default_factory=dict)

    def to_json(self) -> str:
        """序列化为 JSON"""
        return json.dumps({
            "v": self.v,
            "id": self.id,
            "type": self.type,
            "from": self.sender,
            "to": self.receiver,
            "content": self.content,
            "ts": self.ts,
            "reply_to": self.reply_to,
            "meta": self.meta
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> 'AIMMessage':
        """从 JSON 反序列化"""
        obj = json.loads(data)
        return cls(
            v=obj.get("v", 1),
            id=obj.get("id", str(uuid.uuid4())),
            type=obj.get("type", "dm"),
            sender=obj.get("from", ""),
            receiver=obj.get("to", ""),
            content=obj.get("content", ""),
            ts=obj.get("ts", time.time()),
            reply_to=obj.get("reply_to"),
            meta=obj.get("meta", {})
        )


@dataclass
class AgentInfo:
    """Agent 状态信息"""
    agent_id: str
    address: tuple  # (host, port)
    last_seen: float = field(default_factory=time.time)
    online: bool = True


# ============================================================
# UDP 消息内核
# ============================================================

class UDPKernel:
    """UDP 消息内核"""

    def __init__(self, agent_id: str, port: int, host: str = "127.0.0.1"):
        self.agent_id = agent_id
        self.port = port
        self.host = host
        
        # 运行状态
        self.running = False
        self.transport = None
        self.protocol = None
        
        # Agent 状态
        self.agents: Dict[str, AgentInfo] = {}
        
        # 群组管理
        self.groups: Dict[str, Set[str]] = defaultdict(set)
        self.groups["grp_trio"] = {"ZS0001", "ZS0002", "ZS0003", "ZS0005"}
        
        # 离线消息队列
        self.offline_queue: Dict[str, List[AIMMessage]] = defaultdict(list)
        
        # 消息去重
        self.seen_messages: Set[str] = set()
        self.seen_messages_max = 10000
        
        # 回调函数
        self.on_message: Optional[Callable] = None
        self.on_agent_online: Optional[Callable] = None
        self.on_agent_offline: Optional[Callable] = None
        
        # 本地存储路径
        self.storage_dir = Path.home() / ".aim" / "udp-kernel"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    async def start(self):
        """启动内核"""
        log.info(f"[{self.agent_id}] 启动 UDP 内核，监听 {self.host}:{self.port}")
        
        # 创建 UDP 传输
        loop = asyncio.get_event_loop()
        self.transport, self.protocol = await loop.create_datagram_endpoint(
            lambda: UDPProtocol(self),
            local_addr=(self.host, self.port)
        )
        
        self.running = True
        
        # 发送上线通知
        await self.broadcast_join()
        
        # 启动心跳任务
        asyncio.create_task(self._heartbeat_loop())
        
        log.info(f"[{self.agent_id}] UDP 内核启动完成")

    async def stop(self):
        """停止内核"""
        log.info(f"[{self.agent_id}] 停止 UDP 内核")
        
        # 发送离线通知
        await self.broadcast_leave()
        
        self.running = False
        if self.transport:
            self.transport.close()

    async def send_message(self, msg: AIMMessage):
        """发送消息"""
        if not self.running:
            log.error("内核未运行，无法发送消息")
            return False
        
        # 设置发送者
        msg.sender = self.agent_id
        
        # 检查是否重复消息
        if msg.id in self.seen_messages:
            log.debug(f"跳过重复消息: {msg.id}")
            return False
        
        # 记录消息 ID
        self.seen_messages.add(msg.id)
        self._clean_seen_messages()
        
        # 根据消息类型路由
        if msg.type == "dm":
            return await self._send_dm(msg)
        elif msg.type == "group":
            return await self._send_group(msg)
        elif msg.type in ("ping", "pong", "join", "leave"):
            return await self._broadcast(msg)
        else:
            log.warning(f"未知消息类型: {msg.type}")
            return False

    async def _send_dm(self, msg: AIMMessage) -> bool:
        """发送私聊消息"""
        target_id = msg.receiver
        
        # 查找目标 Agent
        agent_info = self.agents.get(target_id)
        if not agent_info or not agent_info.online:
            log.info(f"[{target_id}] 离线，消息加入离线队列")
            self._add_offline_message(target_id, msg)
            return False
        
        # 发送消息
        return await self._send_to(msg, agent_info.address)

    async def _send_group(self, msg: AIMMessage) -> bool:
        """发送群聊消息"""
        group_name = msg.receiver
        members = self.groups.get(group_name, set())
        
        if not members:
            log.warning(f"群组不存在: {group_name}")
            return False
        
        success_count = 0
        for member_id in members:
            if member_id == self.agent_id:
                continue  # 跳过自己
            
            # 创建副本，设置接收者
            member_msg = AIMMessage(
                v=msg.v,
                id=msg.id,
                type=msg.type,
                sender=msg.sender,
                receiver=member_id,
                content=msg.content,
                ts=msg.ts,
                reply_to=msg.reply_to,
                meta=msg.meta
            )
            
            if await self._send_dm(member_msg):
                success_count += 1
        
        log.info(f"群聊消息发送到 {success_count}/{len(members)-1} 个成员")
        return success_count > 0

    async def _broadcast(self, msg: AIMMessage) -> bool:
        """广播消息"""
        data = msg.to_json().encode('utf-8')
        
        try:
            self.transport.sendto(data, (self.host, BROADCAST_PORT))
            
            # 同时发送到所有已知 Agent
            for agent_info in self.agents.values():
                if agent_info.online and agent_info.address:
                    self.transport.sendto(data, agent_info.address)
            
            return True
        except Exception as e:
            log.error(f"广播失败: {e}")
            return False

    async def _send_to(self, msg: AIMMessage, address: tuple) -> bool:
        """发送消息到指定地址"""
        data = msg.to_json().encode('utf-8')
        
        try:
            self.transport.sendto(data, address)
            log.debug(f"消息已发送到 {address}: {msg.id}")
            return True
        except Exception as e:
            log.error(f"发送失败到 {address}: {e}")
            return False

    async def broadcast_join(self):
        """广播上线通知"""
        msg = AIMMessage(
            type="join",
            sender=self.agent_id,
            receiver="*",
            content=f"{self.agent_id} 已上线"
        )
        await self.send_message(msg)

    async def broadcast_leave(self):
        """广播离线通知"""
        msg = AIMMessage(
            type="leave",
            sender=self.agent_id,
            receiver="*",
            content=f"{self.agent_id} 已离线"
        )
        await self.send_message(msg)

    async def _heartbeat_loop(self):
        """心跳循环"""
        while self.running:
            try:
                # 发送心跳
                await self._send_heartbeat()
                
                # 检查超时 Agent
                await self._check_timeout_agents()
                
                # 等待下一次心跳
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"心跳循环错误: {e}")
                await asyncio.sleep(5)

    async def _send_heartbeat(self):
        """发送心跳"""
        msg = AIMMessage(
            type="ping",
            sender=self.agent_id,
            receiver="*",
            content="heartbeat"
        )
        await self.send_message(msg)

    async def _check_timeout_agents(self):
        """检查超时的 Agent"""
        now = time.time()
        timeout_agents = []
        
        for agent_id, info in self.agents.items():
            if info.online and (now - info.last_seen) > HEARTBEAT_TIMEOUT:
                timeout_agents.append(agent_id)
        
        for agent_id in timeout_agents:
            log.info(f"[{agent_id}] 心跳超时，标记为离线")
            self.agents[agent_id].online = False
            
            if self.on_agent_offline:
                await self.on_agent_offline(agent_id)

    def _add_offline_message(self, target_id: str, msg: AIMMessage):
        """添加离线消息"""
        queue = self.offline_queue[target_id]
        
        # 检查队列大小
        if len(queue) >= MAX_OFFLINE_MESSAGES:
            queue.pop(0)  # 移除最旧的消息
        
        queue.append(msg)
        log.info(f"离线消息已保存: {target_id} ({len(queue)} 条)")

    async def flush_offline_messages(self, target_id: str):
        """推送离线消息"""
        if target_id not in self.offline_queue:
            return
        
        queue = self.offline_queue[target_id]
        if not queue:
            return
        
        log.info(f"[{target_id}] 推送 {len(queue)} 条离线消息")
        
        agent_info = self.agents.get(target_id)
        if not agent_info or not agent_info.online:
            return
        
        for msg in queue:
            await self._send_to(msg, agent_info.address)
            await asyncio.sleep(0.1)  # 避免消息乱序
        
        # 清空队列
        del self.offline_queue[target_id]

    def _clean_seen_messages(self):
        """清理已见消息 ID"""
        if len(self.seen_messages) > self.seen_messages_max:
            # 保留最新的 80%
            keep_count = int(self.seen_messages_max * 0.8)
            self.seen_messages = set(list(self.seen_messages)[-keep_count:])

    def handle_message(self, msg: AIMMessage, addr: tuple):
        """处理接收到的消息"""
        # 忽略自己发送的消息
        if msg.sender == self.agent_id:
            return
        
        # 检查重复消息
        if msg.id in self.seen_messages:
            log.debug(f"跳过重复消息: {msg.id}")
            return
        
        self.seen_messages.add(msg.id)
        self._clean_seen_messages()
        
        # 更新 Agent 状态
        if msg.sender not in self.agents:
            self.agents[msg.sender] = AgentInfo(
                agent_id=msg.sender,
                address=addr
            )
            log.info(f"发现新 Agent: {msg.sender} ({addr})")
        
        self.agents[msg.sender].last_seen = time.time()
        self.agents[msg.sender].online = True
        self.agents[msg.sender].address = addr
        
        # 根据消息类型处理
        if msg.type == "ping":
            self._handle_ping(msg, addr)
        elif msg.type == "pong":
            self._handle_pong(msg)
        elif msg.type == "join":
            self._handle_join(msg, addr)
        elif msg.type == "leave":
            self._handle_leave(msg)
        elif msg.type in ("dm", "group"):
            self._handle_chat_message(msg)
        
        # 触发回调
        if self.on_message:
            asyncio.create_task(self.on_message(msg))

    def _handle_ping(self, msg: AIMMessage, addr: tuple):
        """处理心跳请求"""
        pong = AIMMessage(
            type="pong",
            sender=self.agent_id,
            receiver=msg.sender,
            content="pong"
        )
        asyncio.create_task(self._send_to(pong, addr))

    def _handle_pong(self, msg: AIMMessage):
        """处理心跳响应"""
        if msg.sender in self.agents:
            self.agents[msg.sender].last_seen = time.time()

    def _handle_join(self, msg: AIMMessage, addr: tuple):
        """处理上线通知"""
        agent_id = msg.sender
        log.info(f"[{agent_id}] 已上线")
        
        # 更新 Agent 信息
        self.agents[agent_id] = AgentInfo(
            agent_id=agent_id,
            address=addr,
            online=True
        )
        
        # 推送离线消息
        asyncio.create_task(self.flush_offline_messages(agent_id))
        
        # 触发回调
        if self.on_agent_online:
            asyncio.create_task(self.on_agent_online(agent_id))

    def _handle_leave(self, msg: AIMMessage):
        """处理离线通知"""
        agent_id = msg.sender
        log.info(f"[{agent_id}] 已离线")
        
        if agent_id in self.agents:
            self.agents[agent_id].online = False
        
        # 触发回调
        if self.on_agent_offline:
            asyncio.create_task(self.on_agent_offline(agent_id))

    def _handle_chat_message(self, msg: AIMMessage):
        """处理聊天消息"""
        log.info(f"收到消息 [{msg.type}]: {msg.sender} -> {msg.receiver}: {msg.content[:50]}...")


# ============================================================
# UDP 协议实现
# ============================================================

class UDPProtocol(asyncio.DatagramProtocol):
    """UDP 协议处理器"""

    def __init__(self, kernel: UDPKernel):
        self.kernel = kernel

    def connection_made(self, transport):
        """连接建立"""
        log.debug("UDP 连接已建立")

    def datagram_received(self, data: bytes, addr: tuple):
        """接收到数据报"""
        try:
            # 解析消息
            msg = AIMMessage.from_json(data.decode('utf-8'))
            
            # 处理消息
            self.kernel.handle_message(msg, addr)
        except Exception as e:
            log.error(f"消息解析失败: {e}")

    def error_received(self, exc):
        """接收错误"""
        log.error(f"UDP 错误: {exc}")

    def connection_lost(self, exc):
        """连接丢失"""
        log.warning(f"UDP 连接丢失: {exc}")


# ============================================================
# 命令行接口
# ============================================================

async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="AIM UDP 消息内核")
    parser.add_argument("--agent-id", required=True, help="Agent ID (如 ZS0003)")
    parser.add_argument("--port", type=int, help="监听端口 (默认根据 Agent ID 自动分配)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    args = parser.parse_args()
    
    # 确定端口
    port = args.port
    if not port:
        port = DEFAULT_PORTS.get(args.agent_id, 19000 + int(args.agent_id[-2:]))
    
    # 创建内核
    kernel = UDPKernel(args.agent_id, port, args.host)
    
    # 设置回调
    async def on_message(msg: AIMMessage):
        log.info(f"[回调] 收到消息: {msg.type} from {msg.sender}")
    
    kernel.on_message = on_message
    
    # 启动内核
    await kernel.start()
    
    # 保持运行
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await kernel.stop()


if __name__ == "__main__":
    asyncio.run(main())
