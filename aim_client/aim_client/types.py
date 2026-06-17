"""AIM Client — Phase 0 共享类型定义"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time

class AgentState(Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"

class DeliveryMode(Enum):
    REALTIME = "realtime"
    DEFERRED = "deferred"
    BATCH = "batch"

class AdapterStatus(Enum):
    SUCCESS = 0
    RETRY = 1
    DEGRADE = 2
    HUMAN = 3

@dataclass
class StateReport:
    """Monitor 输出的 Runtime 健康状态，Scheduler 只读这个"""
    status: AgentState = AgentState.IDLE
    active_sessions: int = 0
    queue_depth: int = 0
    avg_latency_ms: int = 0
    last_heartbeat: float = field(default_factory=time.time)

@dataclass
class Message:
    """AIM 消息信封"""
    msg_id: str
    from_id: str
    to_id: str = ""
    grp_id: str = ""
    msg_type: str = "dm"  # dm | grp
    content: str = ""
    raw_envelope: dict = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)

@dataclass
class AgentCard:
    """Agent Card Schema v1"""
    global_id: str = ""
    serial: str = ""
    name: str = ""
    execution_model: str = "deferred"  # realtime | deferred | batch
    delivery_mode: str = "deferred"
    max_concurrency: int = 1
    queue_capacity: int = 1000
    preferred_transport: str = "nats"

@dataclass
class AdapterInfo:
    """adapter.sh info 返回的 Runtime 元信息"""
    provider: str = "unknown"
    version: str = "0.0.0"
    execution_model: str = "deferred"
    max_concurrency: int = 1
    supports_streaming: bool = False
