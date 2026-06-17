"""
Transport 层协议抽象（v1.2 方案四）

每种底层协议（NATS / A2A / HTTP / WS）只要实现这 7 个方法，
就能无缝接入 AIM Client，不影响上层任何逻辑。

Phase 0: 直接使用 AIMNATSClient（现有类，天然兼容这 7 个方法）
Phase 1: Transport 抽象层独立，AIM Client 面向 Transport 接口编程
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional


class Transport(ABC):
    """协议无关的传输层抽象

    选择哪种 Transport 取决于部署场景。AIM Client 核心代码**一行不改**，
    切换 Transport 实现类即可。
    """

    @abstractmethod
    async def connect(self, credential: Optional[dict] = None) -> bool:
        """连接到通信网络

        Args:
            credential: 认证凭据（各 Transport 自行决定格式）
                       NATS Transport → JWT creds 文件路径
                       HTTP Transport → Bearer token / mTLS
                       A2A Transport  → OAuth2 / API Key

        Returns:
            True=连接成功, False=失败
        """
        ...

    @abstractmethod
    async def disconnect(self):
        """断开连接"""
        ...

    @abstractmethod
    async def authenticate(self, credential: dict) -> str:
        """认证，返回 AuthToken

        方式由 Transport 实现决定：
        - NATS Transport   → JWT creds 文件
        - HTTP Transport   → Bearer token / mTLS
        - A2A Transport    → OAuth2 / API Key

        Returns:
            AuthToken 字符串，空字符串=认证失败
        """
        ...

    @abstractmethod
    async def verify_peer(self, peer_id: str, signature: bytes) -> bool:
        """验证对端身份签名（高安全场景可选）

        Args:
            peer_id: 对端 Agent ID
            signature: 签名数据

        Returns:
            True=身份验证通过, False=验证失败
        """
        ...

    @abstractmethod
    async def subscribe(self, subject: str, callback: Callable) -> str:
        """订阅主题

        Args:
            subject: 主题名（如 aim.dm.ZS0002、aim.grp.grp_trio）
            callback: 收到消息时的回调函数

        Returns:
            订阅 ID（可用于取消订阅）
        """
        ...

    @abstractmethod
    async def publish(self, subject: str, payload: dict) -> bool:
        """发布消息到指定主题

        Args:
            subject: 目标主题
            payload: 消息内容（dict，会被序列化为 JSON）

        Returns:
            True=发布成功, False=失败
        """
        ...

    @abstractmethod
    async def request(self, subject: str, payload: dict, timeout: float) -> dict:
        """请求-回复模式

        发送请求并等待回复（realtime 模式专用）。
        deferred/batch 模式的 Agent 不应使用此方法。

        Args:
            subject: 目标主题
            payload: 请求内容
            timeout: 超时秒数

        Returns:
            回复内容 dict。超时返回 {"status": "timeout"}
        """
        ...


class TransportCapabilities:
    """Transport 能力描述

    用于 Scheduler 选择投递策略时的参考信息。
    """

    def __init__(
        self,
        supports_jetstream: bool = False,
        supports_request_reply: bool = False,
        supports_broadcast: bool = False,
        max_message_size: int = 1024 * 1024,
    ):
        self.supports_jetstream = supports_jetstream
        self.supports_request_reply = supports_request_reply
        self.supports_broadcast = supports_broadcast
        self.max_message_size = max_message_size


class NATSTransport(Transport):
    """NATS Transport 实现

    Phase 0-1 的主要 Transport。基于 AIMNATSClient 实现 7 方法。
    注：verify_peer 在 Phase 0-1 返回 True（JWT 层面已认证），
    Phase 2+ 可扩展为签名验证。
    """

    def __init__(self, client):
        """
        Args:
            client: AIMNATSClient 实例
        """
        self._client = client
        self._subscriptions: dict[str, str] = {}

    async def connect(self, credential: Optional[dict] = None) -> bool:
        try:
            await self._client.connect()
            return True
        except Exception:
            return False

    async def disconnect(self):
        await self._client.disconnect()

    async def authenticate(self, credential: dict) -> str:
        # NATS 在 connect 时已通过 JWT creds 认证
        return self._client.agent_id

    async def verify_peer(self, peer_id: str, signature: bytes) -> bool:
        # Phase 0-1：信任 JWT（NATS 层面已认证对端）
        return True

    async def subscribe(self, subject: str, callback: Callable) -> str:
        import uuid
        sub_id = f"sub_{uuid.uuid4().hex[:12]}"
        sub = await self._client.nc.subscribe(subject, cb=callback)
        self._subscriptions[sub_id] = str(id(sub))
        return sub_id

    async def publish(self, subject: str, payload: dict) -> bool:
        import json
        try:
            data = json.dumps(payload, ensure_ascii=False).encode()
            await self._client.nc.publish(subject, data)
            return True
        except Exception:
            return False

    async def request(self, subject: str, payload: dict, timeout: float) -> dict:
        import json
        try:
            data = json.dumps(payload, ensure_ascii=False).encode()
            msg = await self._client.nc.request(subject, data, timeout=timeout)
            return json.loads(msg.data.decode())
        except Exception:
            return {"status": "timeout"}

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            supports_jetstream=True,
            supports_request_reply=True,
            supports_broadcast=True,
            max_message_size=1024 * 1024,
        )
