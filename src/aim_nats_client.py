#!/usr/bin/env python3
"""
AIM NATS Client SDK
统一的 NATS 客户端封装，供所有 Agent 使用
"""

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Callable, Optional, Dict, List

import nats
from nats.js.api import StreamConfig, ConsumerConfig


class AIMNatsClient:
    """AIM NATS 客户端"""
    
    def __init__(self, agent_id: str, nats_url: str = "nats://127.0.0.1:4222"):
        self.agent_id = agent_id
        self.nats_url = nats_url
        self.nc = None
        self.js = None
        self.subscriptions = {}
        self.message_handlers = {}
        
    async def connect(self):
        """连接到 NATS Server"""
        self.nc = await nats.connect(
            self.nats_url,
            max_reconnect_attempts=-1,
            reconnect_time_wait=2,
            ping_interval=10,
            max_outstanding_pings=3
        )
        self.js = self.nc.jetstream()
        print(f"[{self.agent_id}] 已连接到 NATS Server")
        
    async def disconnect(self):
        """断开连接"""
        if self.nc:
            await self.nc.close()
            print(f"[{self.agent_id}] 已断开连接")
            
    async def setup_streams(self):
        """设置 JetStream — 使用 aim-veritas 命名规范"""
        try:
            await self.js.add_stream(
                name="AIM_MESSAGES",
                subjects=[
                    "aim.dm.>",
                    "aim.grp.>",
                    "aim.obs.>",
                    "aim.sys.*",
                    "aim.reg.*",  # reg.* 仅用于注册状态记录, request-reply 走 Core NATS
                ],
                storage="file",
                retention="limits",
                max_age=7 * 24 * 3600,  # 7 天
                max_msgs=100000,
                max_bytes=1_073_741_824,  # 1GB
                duplicate_window=120,  # 120 秒去重窗口
            )
            print(f"[{self.agent_id}] 创建 Stream: AIM_MESSAGES")
        except Exception as e:
            print(f"[{self.agent_id}] Stream 已存在: {e}")
            
    async def subscribe_private_messages(self, handler: Callable):
        """订阅私聊消息 (aim.dm.<id>)"""
        subject = f"aim.dm.{self.agent_id}"
        sub = await self.nc.subscribe(subject, cb=handler)
        self.subscriptions["private"] = sub
        print(f"[{self.agent_id}] 订阅私聊消息: {subject}")
        
    async def subscribe_group_messages(self, group_id: str, handler: Callable):
        """订阅群聊消息 (aim.grp.<group_id>)"""
        subject = f"aim.grp.{group_id}"
        sub = await self.nc.subscribe(subject, cb=handler)
        self.subscriptions[f"group_{group_id}"] = sub
        print(f"[{self.agent_id}] 订阅群聊消息: {subject}")
        
    async def subscribe_observer_events(self, handler: Callable):
        """订阅 Observer 事件 (aim.obs.>)"""
        subject = "aim.obs.>"
        sub = await self.nc.subscribe(subject, cb=handler)
        self.subscriptions["observer"] = sub
        print(f"[{self.agent_id}] 订阅 Observer 事件: {subject}")
        
    async def send_private_message(self, to_id: str, content: str, metadata: Optional[Dict] = None):
        """发送私聊消息 (aim.dm.<to_id>)"""
        msg = {
            "msg_id": str(uuid.uuid4()),
            "from": self.agent_id,
            "to": to_id,
            "type": "dm",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        subject = f"aim.dm.{to_id}"
        await self.nc.publish(subject, json.dumps(msg).encode())
        print(f"[{self.agent_id}] 发送私聊消息给 {to_id}: {content[:50]}...")
        return msg
        
    async def send_group_message(self, group_id: str, content: str, metadata: Optional[Dict] = None):
        """发送群聊消息 (aim.grp.<group_id>)"""
        msg = {
            "msg_id": str(uuid.uuid4()),
            "from": self.agent_id,
            "group": group_id,
            "type": "group",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        subject = f"aim.grp.{group_id}"
        await self.nc.publish(subject, json.dumps(msg).encode())
        print(f"[{self.agent_id}] 发送群聊消息到 {group_id}: {content[:50]}...")
        return msg
        
    async def request(self, to_id: str, content: str, timeout: int = 5):
        """发送请求并等待响应（使用 aim.req.* 避免 JetStream 劫持）"""
        msg = {
            "msg_id": str(uuid.uuid4()),
            "from": self.agent_id,
            "to": to_id,
            "type": "request",
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        subject = f"aim.req.{to_id}"  # 不进入 JetStream 管理范围
        response = await self.nc.request(
            subject,
            json.dumps(msg).encode(),
            timeout=timeout
        )
        return json.loads(response.data)
        
    async def emit_observer_event(self, event_type: str, detail: str):
        """发送 Observer 事件 (aim.obs.<agent_id>)"""
        event = {
            "type": event_type,
            "agent_id": self.agent_id,
            "detail": detail,
            "ts": time.time()
        }
        subject = f"aim.obs.{self.agent_id}"
        await self.nc.publish(subject, json.dumps(event).encode())
        
    async def publish_with_jetstream(self, subject: str, content: str):
        """使用 JetStream 发送消息（持久化）"""
        msg = {
            "msg_id": str(uuid.uuid4()),
            "from": self.agent_id,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        ack = await self.js.publish(subject, json.dumps(msg).encode())
        print(f"[{self.agent_id}] JetStream 发送消息, sequence: {ack.seq}")
        return ack
        
    async def subscribe_with_jetstream(self, subject: str, durable: str, handler: Callable):
        """使用 JetStream 订阅消息（持久化）"""
        sub = await self.js.subscribe(
            subject,
            durable=durable,
            cb=handler
        )
        self.subscriptions[f"js_{durable}"] = sub
        print(f"[{self.agent_id}] JetStream 订阅: {subject} (durable: {durable})")
        
    def is_connected(self) -> bool:
        """检查连接状态"""
        return self.nc and self.nc.is_connected
        
    def get_stats(self) -> Dict:
        """获取统计信息"""
        if not self.nc:
            return {}
        return {
            "out_msgs": self.nc.stats.get("out_msgs", 0),
            "out_bytes": self.nc.stats.get("out_bytes", 0),
            "in_msgs": self.nc.stats.get("in_msgs", 0),
            "in_bytes": self.nc.stats.get("in_bytes", 0)
        }


# 示例用法
async def example_usage():
    """示例用法"""
    # 创建客户端
    client = AIMNatsClient("ZS0001")
    
    # 连接
    await client.connect()
    
    # 设置 Stream
    await client.setup_streams()
    
    # 定义消息处理器
    async def on_private_msg(msg):
        data = json.loads(msg.data)
        print(f"收到私聊消息: {data['content']}")
        
    async def on_group_msg(msg):
        data = json.loads(msg.data)
        print(f"收到群聊消息: {data['from']}: {data['content']}")
        
    # 订阅
    await client.subscribe_private_messages(on_private_msg)
    await client.subscribe_group_messages("grp_trio", on_group_msg)
    
    # 发送消息
    await client.send_private_message("ZS0002", "你好，吉量！")
    await client.send_group_message("grp_trio", "大家好！")
    
    # 发送 Observer 事件
    await client.emit_observer_event("message", "发送消息给 ZS0002")
    
    # 保持运行
    await asyncio.sleep(60)
    
    # 断开连接
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(example_usage())
