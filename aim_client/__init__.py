"""AIM Client — OAS 公民接入层

Phase 0：嵌入 V3 验证 Queue + Scheduler
Phase 1：独立 aim-client 进程 + 三级降级模型
"""
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
