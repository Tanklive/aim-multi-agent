"""AIM Client — OAS 公民接入层

Phase 0：嵌入 V3 验证 Queue + Scheduler
v1.2：独立 aim-client 进程 + 三级降级模型
"""

VERSION = "1.4.0"
from .types import (
    AgentState, DegradeLevel, StateReport, Message,
    AgentCard, AdapterInfo, DeliveryMode,
    evaluate_degrade_level, get_probe_interval, make_degrade_event,
)
from .queue import MessageQueue, QueueStats
from .scheduler import Scheduler, SchedulerEvent
from .health_probe import HealthProbe

__all__ = [
    "AgentState", "DegradeLevel", "StateReport", "Message",
    "AgentCard", "AdapterInfo", "DeliveryMode",
    "evaluate_degrade_level", "get_probe_interval", "make_degrade_event",
    "MessageQueue", "QueueStats",
    "Scheduler", "SchedulerEvent",
    "HealthProbe",
]
