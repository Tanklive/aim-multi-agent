"""
AIM NATS 客户端封装
统一的 NATS 连接、认证、消息收发接口
"""

import asyncio
import json
import time
import uuid
import logging
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass, field

try:
    import nats
    from nats.aio.client import Client as NATSClient
except ImportError:
    raise ImportError("pip install nats-py")

log = logging.getLogger("aim-nats")


# ── 数据模型 ──────────────────────────────────────────

@dataclass
class AIMMessage:
    """AIM 消息"""
    msg_id: str = ""
    from_id: str = ""
    to_id: str = ""
    group_id: str = ""
    content: str = ""
    msg_type: str = "text"
    seq: int = 0
    ts: float = 0.0
    retry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id or str(uuid.uuid4())[:12],
            "from_id": self.from_id,
            "to_id": self.to_id,
            "group_id": self.group_id,
            "content": self.content,
            "msg_type": self.msg_type,
            "seq": self.seq,
            "ts": self.ts or time.time(),
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AIMMessage":
        return cls(
            msg_id=data.get("msg_id", ""),
            from_id=data.get("from_id", ""),
            to_id=data.get("to_id", ""),
            group_id=data.get("group_id", ""),
            content=data.get("content", ""),
            msg_type=data.get("msg_type", "text"),
            seq=data.get("seq", 0),
            ts=data.get("ts", 0),
            retry_count=data.get("retry_count", 0),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "AIMMessage":
        return cls.from_dict(json.loads(raw))


# ── Subject 设计 ──────────────────────────────────────

class Subjects:
    """NATS Subject 命名规范"""

    @staticmethod
    def private_msg(agent_id: str) -> str:
        """私聊消息 Subject"""
        return f"agent.{agent_id}.msg"

    @staticmethod
    def group_msg(group_id: str) -> str:
        """群聊消息 Subject"""
        return f"group.{group_id}.msg"

    @staticmethod
    def request(agent_id: str) -> str:
        """请求 Subject"""
        return f"agent.{agent_id}.request"

    @staticmethod
    def response(agent_id: str) -> str:
        """响应 Subject"""
        return f"agent.{agent_id}.response"

    @staticmethod
    def observer_events() -> str:
        """Observer 事件 Subject"""
        return "observer.events"

    @staticmethod
    def observer_event_type(event_type: str) -> str:
        """特定类型 Observer 事件"""
        return f"observer.events.{event_type}"


# ── AIM NATS 客户端 ──────────────────────────────────

class AIMNATSClient:
    """AIM NATS 客户端"""

    def __init__(self, agent_id: str, server: str = "nats://127.0.0.1:4222"):
        self.agent_id = agent_id
        self.server = server
        self.nc: Optional[NATSClient] = None
        self.js = None  # JetStream 上下文
        self._subscriptions: Dict[str, Any] = {}
        self._msg_handler: Optional[Callable] = None
        self._running = False

    async def connect(self):
        """连接 NATS Server"""
        log.info(f"🔗 连接 NATS: {self.server}")
        self.nc = await nats.connect(
            self.server,
            max_reconnect_attempts=-1,
            reconnect_time_wait=2,
            ping_interval=10,
            max_outstanding_pings=3,
        )
        self.js = self.nc.jetstream()
        self._running = True
        log.info(f"✅ 已连接: {self.agent_id} | NATS: {self.server}")

    async def disconnect(self):
        """断开连接"""
        self._running = False
        if self.nc:
            await self.nc.drain()
            log.info("🔌 已断开 NATS 连接")

    # ── 订阅 ──────────────────────────────────────────

    def _wrap_handler(self, handler: Callable):
        """将 handler 包装为 coroutine（nats-py v2 要求 cb 是 coroutine）"""
        async def _cb(msg):
            await self._dispatch(msg, handler)
        return _cb

    async def subscribe_private(self, handler: Callable):
        """订阅私聊消息"""
        subject = Subjects.private_msg(self.agent_id)
        sub = await self.nc.subscribe(subject, cb=self._wrap_handler(handler))
        self._subscriptions[subject] = sub
        log.info(f"📩 订阅私聊: {subject}")

    async def subscribe_group(self, group_id: str, handler: Callable):
        """订阅群聊消息"""
        subject = Subjects.group_msg(group_id)
        sub = await self.nc.subscribe(subject, cb=self._wrap_handler(handler))
        self._subscriptions[subject] = sub
        log.info(f"📩 订阅群聊: {subject}")

    async def subscribe_request(self, handler: Callable):
        """订阅请求（用于 request-reply）"""
        subject = Subjects.request(self.agent_id)
        sub = await self.nc.subscribe(subject, cb=self._wrap_handler(handler))
        self._subscriptions[subject] = sub
        log.info(f"📩 订阅请求: {subject}")

    async def subscribe_observer(self, handler: Callable, event_type: str = ">"):
        """订阅 Observer 事件"""
        subject = f"{Subjects.observer_events()}.{event_type}"
        sub = await self.nc.subscribe(subject, cb=self._wrap_handler(handler))
        self._subscriptions[subject] = sub
        log.info(f"👁️ 订阅 Observer: {subject}")

    async def _dispatch(self, msg, handler: Callable):
        """分发消息到处理器"""
        try:
            data = json.loads(msg.data.decode())
            await handler(AIMMessage.from_dict(data), msg)
        except Exception as e:
            log.error(f"消息处理异常: {e}")

    # ── 发送 ──────────────────────────────────────────

    async def send_private(self, to_id: str, content: str, msg_type: str = "text") -> AIMMessage:
        """发送私聊消息"""
        msg = AIMMessage(
            from_id=self.agent_id,
            to_id=to_id,
            content=content,
            msg_type=msg_type,
        )
        subject = Subjects.private_msg(to_id)
        await self.nc.publish(subject, msg.to_json().encode())
        log.info(f"📤 私聊发送: {to_id} | {content[:50]}")
        return msg

    async def send_group(self, group_id: str, content: str, msg_type: str = "text") -> AIMMessage:
        """发送群聊消息"""
        msg = AIMMessage(
            from_id=self.agent_id,
            group_id=group_id,
            content=content,
            msg_type=msg_type,
        )
        subject = Subjects.group_msg(group_id)
        await self.nc.publish(subject, msg.to_json().encode())
        log.info(f"📤 群聊发送: {group_id} | {content[:50]}")
        return msg

    async def request(self, to_id: str, content: str, timeout: float = 5.0) -> AIMMessage:
        """发送请求并等待响应"""
        msg = AIMMessage(
            from_id=self.agent_id,
            to_id=to_id,
            content=content,
        )
        subject = Subjects.request(to_id)
        response = await self.nc.request(subject, msg.to_json().encode(), timeout=timeout)
        return AIMMessage.from_json(response.data.decode())

    # ── Observer 事件 ─────────────────────────────────

    async def emit_event(self, event_type: str, detail: str, **kwargs):
        """发送 Observer 事件"""
        event = {
            "type": event_type,
            "agent_id": self.agent_id,
            "detail": detail,
            "ts": time.time(),
            **kwargs,
        }
        subject = Subjects.observer_event_type(event_type)
        await self.nc.publish(subject, json.dumps(event, ensure_ascii=False).encode())

    # ── JetStream 持久化 ──────────────────────────────

    async def setup_jetstream(self):
        """设置 JetStream Stream 和 Consumer"""
        try:
            # 创建 Stream
            await self.js.add_stream(
                name="AIM_MESSAGES",
                subjects=["agent.*.msg", "group.*.msg"],
                storage="file",
                max_age=7 * 24 * 3600,  # 7天
                max_msgs=100000,
                duplicate_window=120,  # 120秒去重窗口
            )
            log.info("✅ JetStream Stream 已创建: AIM_MESSAGES")

            # 创建 Consumer
            await self.js.add_consumer(
                "AIM_MESSAGES",
                durable_name=f"agent-{self.agent_id}",
                filter_subjects=[
                    Subjects.private_msg(self.agent_id),
                    "group.grp_trio.msg",
                ],
                ack_policy="explicit",
                deliver_policy="all",
                max_deliver=5,
                ack_wait=30,
            )
            log.info(f"✅ JetStream Consumer 已创建: agent-{self.agent_id}")

        except Exception as e:
            log.warning(f"JetStream 设置: {e}")

    async def send_persistent(self, to_id: str, content: str) -> AIMMessage:
        """发送持久化消息（通过 JetStream）"""
        msg = AIMMessage(
            from_id=self.agent_id,
            to_id=to_id,
            content=content,
        )
        subject = Subjects.private_msg(to_id)
        ack = await self.js.publish(subject, msg.to_json().encode())
        msg.seq = ack.seq
        log.info(f"📤 持久化发送: {to_id} | seq={ack.seq}")
        return msg

    async def consume_persistent(self, handler: Callable):
        """消费持久化消息"""
        async for msg in self.js.subscribe(
            Subjects.private_msg(self.agent_id),
            durable=f"agent-{self.agent_id}",
        ):
            data = AIMMessage.from_json(msg.data.decode())
            try:
                await handler(data, msg)
                await msg.ack()
            except Exception as e:
                log.error(f"持久化消息处理失败: {e}")
                await msg.nak()

    # ── 状态 ──────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self.nc is not None and self.nc.is_connected

    def status(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "server": self.server,
            "connected": self.is_connected,
            "subscriptions": list(self._subscriptions.keys()),
        }