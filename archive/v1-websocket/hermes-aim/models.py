"""AIM 数据模型"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
import hashlib
import hmac
import json
import uuid


class MsgType(str, Enum):
    TEXT = "text"
    SYSTEM = "system"
    ACK = "ack"
    ERROR = "error"


@dataclass
class Message:
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    msg_type: str = MsgType.TEXT
    from_id: str = ""
    to_id: str = ""
    content: str = ""
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    group: bool = False
    hop: int = 0  # 路由跳数，防环
    source_type: str = "agent"  # 消息来源类型: agent / system / human
    
    # 任务状态追踪（可选字段）
    task: Optional[dict] = None  # 任务元数据: {task_id, type, priority, deadline, estimated_range}
    status: Optional[dict] = None  # 状态: {state, ts, by}

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "Message":
        d = json.loads(data)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_log_line(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return json.dumps({
            "msg_id": self.msg_id, "type": self.msg_type,
            "from": self.from_id, "to": self.to_id,
            "content": self.content, "group": self.group,
            "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": self.timestamp,
        }, ensure_ascii=False)


@dataclass
class PeerInfo:
    """对等节点信息"""
    agent_id: str
    name: str
    emoji: str
    token_hash: str = ""    # SHA256(agent_id:token)，config中存储
    token: str = ""          # 明文token，仅本节点token.json中
    host: str = "127.0.0.1"
    port: int = 18900
    role: str = "member"
    online: bool = False

    @property
    def ws_url(self) -> str:
        # 0.0.0.0 是监听地址，客户端应连 127.0.0.1
        host = self.host if self.host != "0.0.0.0" else "127.0.0.1"
        return f"ws://{host}:{self.port}"

    def verify_token(self, agent_id: str, token: str) -> bool:
        """验证token：HMAC-SHA256(agent_id, token) == 存储的hash"""
        # 使用HMAC-SHA256，密钥为agent_id，消息为token
        h = hmac.new(agent_id.encode(), token.encode(), hashlib.sha256).hexdigest()
        return h == self.token_hash

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id, "name": self.name,
            "emoji": self.emoji, "role": self.role,
            "online": self.online, "host": self.host, "port": self.port,
        }


@dataclass
class ConnInfo:
    """连接信息 — 每个 WebSocket 连接的元数据"""
    ws: object          # WebSocket 连接对象
    channel: str = "main"   # 连接频道
    handler: bool = False   # 是否为 handler（负责 AI 处理）
    term: int = 1           # 任期号（断连重连 +1）
    connected_at: float = field(default_factory=lambda: datetime.now().timestamp())
    label: str = ""         # 可选标签
    disconnecting: bool = False  # 是否在优雅窗口期
    grace_task: object = field(default=None, repr=False)  # 优雅窗口清理任务

    @property
    def age_seconds(self) -> float:
        return datetime.now().timestamp() - self.connected_at

    @property
    def conn_id(self) -> str:
        return f"{self.channel}:{id(self.ws)}"

    def mark_disconnecting(self):
        self.disconnecting = True

    def is_active(self) -> bool:
        return not self.disconnecting
