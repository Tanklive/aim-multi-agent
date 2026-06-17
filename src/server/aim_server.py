#!/usr/bin/env python3
"""
AIM NATS Server — 精简版
只保留业务逻辑，删除所有 WebSocket/连接池/重试代码
"""

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

import nats
from nats.js.api import StreamConfig

# ── 常量 ─────────────────────────────

BASE_DIR = Path(__file__).parent
NATS_URL = os.environ.get("AIM_NATS_URL", "nats://127.0.0.1:4222")

# JetStream 配置
# 注意: agent.*.request 和 agent.*.response 不加入 Stream
# 因为 request-reply 是点对点同步通信，不需要 JetStream 持久化
# 如果加入会被 JetStream 劫持 reply-to，导致 response 内容为空
STREAM_NAME = "AIM_MESSAGES"
SUBJECTS = [
    "agent.*.msg",
    "group.*.msg"
]

# 注册表配置
HEARTBEAT_TIMEOUT = 900  # 15min 无心跳标记 offline
REGISTRY_EXPIRE_SECONDS = 86400  # 24h 无有效连接 → 清理注册


# ── 数据模型 ─────────────────────────

@dataclass
class RegisteredAgent:
    """已注册的 Agent"""
    agent_id: str
    agent_name: str
    emoji: str = "🤖"
    framework: str = ""
    version: str = ""
    status: str = "offline"  # online | offline | cooldown
    last_heartbeat: float = 0.0
    connected_at: float = 0.0
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "emoji": self.emoji,
            "framework": self.framework,
            "version": self.version,
            "status": self.status,
            "last_heartbeat": self.last_heartbeat,
            "connected_at": self.connected_at,
            "metadata": self.metadata,
        }


# ── AIM NATS Server ─────────────────

class AIMNatsServer:
    """AIM NATS Server — 精简版"""

    def __init__(self, nats_url: str = NATS_URL, credentials: str = ""):
        self.nats_url = nats_url
        self.credentials = credentials
        self.nc = None
        self.js = None
        self.agents: Dict[str, RegisteredAgent] = {}
        self.subscriptions = {}

    async def connect(self):
        """连接到 NATS Server"""
        kwargs = {
            "max_reconnect_attempts": -1,
            "reconnect_time_wait": 2,
            "ping_interval": 10,
            "max_outstanding_pings": 3
        }
        if self.credentials:
            if os.path.isfile(self.credentials):
                kwargs["user_credentials"] = self.credentials
            else:
                kwargs["token"] = self.credentials

        self.nc = await nats.connect(self.nats_url, **kwargs)
        self.js = self.nc.jetstream()
        print(f"[Server] 已连接到 NATS Server: {self.nats_url}")

    async def disconnect(self):
        """断开连接"""
        if self.nc:
            await self.nc.close()
            print(f"[Server] 已断开连接")

    async def setup_streams(self):
        """设置 JetStream"""
        try:
            await self.js.add_stream(
                name=STREAM_NAME,
                subjects=SUBJECTS,
                storage="file",
                retention="limits",
                max_age=7 * 24 * 3600,  # 7 天 (秒)
                max_msgs=100000,
                max_bytes=1_073_741_824,  # 1GB
                duplicate_window=120,  # 120 秒去重窗口
            )
            print(f"[Server] 创建 Stream: {STREAM_NAME}")
        except Exception as e:
            print(f"[Server] Stream 已存在: {e}")

    # ── 注册表 ─────────────────────────

    def register_agent(self, agent_id: str, agent_name: str, **kwargs) -> RegisteredAgent:
        """注册 Agent"""
        if agent_id in self.agents:
            # 更新现有注册
            agent = self.agents[agent_id]
            agent.agent_name = agent_name
            agent.last_heartbeat = time.time()
            agent.status = "online"
            for k, v in kwargs.items():
                if hasattr(agent, k):
                    setattr(agent, k, v)
        else:
            # 新注册
            agent = RegisteredAgent(
                agent_id=agent_id,
                agent_name=agent_name,
                last_heartbeat=time.time(),
                connected_at=time.time(),
                status="online",
                **kwargs
            )
            self.agents[agent_id] = agent

        print(f"[Server] Agent 注册: {agent_id} ({agent_name})")
        return agent

    def unregister_agent(self, agent_id: str):
        """注销 Agent"""
        if agent_id in self.agents:
            self.agents[agent_id].status = "offline"
            print(f"[Server] Agent 注销: {agent_id}")

    def get_agent(self, agent_id: str) -> Optional[RegisteredAgent]:
        """获取 Agent 信息"""
        return self.agents.get(agent_id)

    def list_agents(self) -> List[RegisteredAgent]:
        """列出所有 Agent"""
        return list(self.agents.values())

    def update_heartbeat(self, agent_id: str):
        """更新心跳"""
        if agent_id in self.agents:
            self.agents[agent_id].last_heartbeat = time.time()
            self.agents[agent_id].status = "online"

    def check_heartbeats(self):
        """检查心跳超时"""
        now = time.time()
        for agent_id, agent in self.agents.items():
            if agent.status == "online" and now - agent.last_heartbeat > HEARTBEAT_TIMEOUT:
                agent.status = "offline"
                print(f"[Server] Agent 心跳超时: {agent_id}")

    # ── Observer 事件 ──────────────────

    async def emit_observer_event(self, event_type: str, agent_id: str, detail: str):
        """发送 Observer 事件"""
        event = {
            "type": event_type,
            "agent_id": agent_id,
            "detail": detail,
            "ts": time.time()
        }
        subject = f"observer.events.{event_type}"
        await self.nc.publish(subject, json.dumps(event).encode())
        print(f"[Server] Observer 事件: {event_type} from {agent_id}")

    # ── 消息处理 ────────────────────────

    async def handle_private_message(self, msg):
        """处理私聊消息"""
        try:
            data = json.loads(msg.data)
            to_id = data.get("to")
            from_id = data.get("from")

            # 更新心跳
            self.update_heartbeat(from_id)

            # 发送 Observer 事件
            await self.emit_observer_event("message", from_id, f"发送消息给 {to_id}")

            print(f"[Server] 私聊消息: {from_id} → {to_id}")
        except json.JSONDecodeError as e:
            print(f"[Server] 消息解析错误: {e}, 数据: {msg.data[:100]}")
        except Exception as e:
            print(f"[Server] 处理消息错误: {e}")

    async def handle_group_message(self, msg):
        """处理群聊消息"""
        try:
            data = json.loads(msg.data)
            group_id = data.get("group")
            from_id = data.get("from")

            # 更新心跳
            self.update_heartbeat(from_id)

            # 发送 Observer 事件
            await self.emit_observer_event("message", from_id, f"发送群聊消息到 {group_id}")

            print(f"[Server] 群聊消息: {from_id} → {group_id}")
        except json.JSONDecodeError as e:
            print(f"[Server] 群聊消息解析错误: {e}, 数据: {msg.data[:100]}")
        except Exception as e:
            print(f"[Server] 处理群聊消息错误: {e}")

    # ── 订阅 ────────────────────────────

    async def subscribe_all(self):
        """订阅所有消息"""
        # 订阅私聊消息
        async def on_private_msg(msg):
            await self.handle_private_message(msg)

        # 订阅群聊消息
        async def on_group_msg(msg):
            await self.handle_group_message(msg)

        # 订阅 Observer 事件
        async def on_observer_event(msg):
            data = json.loads(msg.data)
            print(f"[Server] Observer 事件: {data}")

        # 订阅所有 Agent 的私聊消息
        sub_private = await self.nc.subscribe("agent.*.msg", cb=on_private_msg)
        self.subscriptions["private"] = sub_private

        # 订阅所有群聊消息
        sub_group = await self.nc.subscribe("group.*.msg", cb=on_group_msg)
        self.subscriptions["group"] = sub_group

        # 订阅 Observer 事件
        sub_observer = await self.nc.subscribe("observer.events.>", cb=on_observer_event)
        self.subscriptions["observer"] = sub_observer

        # ── 注册处理器 ──
        async def on_register(msg):
            """处理 Agent 注册请求"""
            try:
                data = json.loads(msg.data.decode())
                agent_id = data.get("agent_id", "")
                agent_name = data.get("name", data.get("agent_name", agent_id))
                framework = data.get("framework", "unknown")

                # 调用注册方法
                agent = self.register_agent(agent_id, agent_name, framework=framework)

                # 返回注册结果
                response = {
                    "status": "ok",
                    "agent_id": agent_id,
                    "name": agent_name,
                    "framework": framework,
                    "server_time": time.time(),
                    "message": f"Agent {agent_id} ({agent_name}) 注册成功"
                }
                await msg.respond(json.dumps(response).encode())
                print(f"[Server] 注册请求处理: {agent_id} ({agent_name}) ✅")
            except Exception as e:
                error_resp = {"status": "error", "message": str(e)}
                try:
                    await msg.respond(json.dumps(error_resp).encode())
                except:
                    pass
                print(f"[Server] 注册请求错误: {e}")

        sub_register = await self.nc.subscribe("aim.reg.register", cb=on_register)
        self.subscriptions["register"] = sub_register

        # ── 心跳处理器 ──
        async def on_heartbeat(msg):
            """处理 Agent 心跳"""
            try:
                data = json.loads(msg.data.decode())
                agent_id = data.get("agent_id", "")
                self.update_heartbeat(agent_id)
            except Exception as e:
                print(f"[Server] 心跳处理错误: {e}")

        sub_heartbeat = await self.nc.subscribe("aim.sys.heartbeat", cb=on_heartbeat)
        self.subscriptions["heartbeat"] = sub_heartbeat

        print(f"[Server] 已订阅所有消息（含注册+心跳）")

        # 订阅 Agent 列表查询
        async def on_list(msg):
            agents = [
                {
                    "agent_id": a.agent_id,
                    "name": a.agent_name,
                    "status": a.status,
                    "framework": getattr(a, 'framework', 'unknown'),
                    "last_heartbeat": a.last_heartbeat,
                    "connected_at": a.connected_at
                }
                for a in self.list_agents()
            ]
            await msg.respond(json.dumps({"agents": agents}).encode())

        sub_list = await self.nc.subscribe("aim.reg.list", cb=on_list)
        self.subscriptions["list_agents"] = sub_list
        print(f"[Server] 已注册 Agent 列表查询")

    # ── 心跳检查循环 ────────────────────

    async def heartbeat_loop(self):
        """心跳检查循环"""
        while True:
            self.check_heartbeats()
            await asyncio.sleep(60)  # 每分钟检查一次

    # ── 运行 ────────────────────────────

    async def run(self):
        """运行 Server"""
        print(f"[Server] 启动 AIM NATS Server...")

        # 连接
        await self.connect()

        # 设置 Stream
        await self.setup_streams()

        # 订阅所有消息
        await self.subscribe_all()

        # 启动心跳检查
        asyncio.create_task(self.heartbeat_loop())

        print(f"[Server] AIM NATS Server 已启动")
        print(f"[Server] NATS URL: {self.nats_url}")
        print(f"[Server] Stream: {STREAM_NAME}")
        print(f"[Server] 等待消息...")

        # 保持运行
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print(f"[Server] 收到停止信号...")
        finally:
            await self.disconnect()
            print(f"[Server] AIM NATS Server 已停止")


# ── CLI 接口 ──────────────────────────

def load_config():
    """从 ~/.aim/config/aim.json 加载配置"""
    config_path = os.path.expanduser("~/.aim/config/aim.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


def main():
    """CLI 入口"""
    import argparse

    config = load_config()

    parser = argparse.ArgumentParser(description="AIM NATS Server")
    parser.add_argument("--nats-url",
                        default=os.environ.get("AIM_NATS_URL", config.get("nats_server", NATS_URL)),
                        help="NATS Server URL")
    parser.add_argument("--credentials",
                        default=os.environ.get("AIM_NATS_TOKEN", config.get("nats_token", "")),
                        help="NATS auth token or credentials file path")
    args = parser.parse_args()

    server = AIMNatsServer(nats_url=args.nats_url, credentials=args.credentials)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
