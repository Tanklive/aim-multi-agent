"""AIM Client — OAS 公民接入层

Phase 0：嵌入 V3 验证 Queue + Scheduler
v1.2：独立 aim-client 进程 + 三级降级模型
v1.6.0：+ HotFeed 热冷消息分级机制（平台级通用模块）
"""

VERSION = "1.6.0"
from .types import (
    AgentState, DegradeLevel, StateReport, Message,
    AgentCard, AdapterInfo, DeliveryMode,
    evaluate_degrade_level, get_probe_interval, make_degrade_event,
)
from .queue import MessageQueue, QueueStats
from .scheduler import Scheduler, SchedulerEvent
from .health_probe import HealthProbe
from .hot_feed import (
    AIMHotFeed, HotFeedReport, HotMessage,
    DEFAULT_POLICY, attach_to_client,
)

__all__ = [
    "AgentState", "DegradeLevel", "StateReport", "Message",
    "AgentCard", "AdapterInfo", "DeliveryMode",
    "evaluate_degrade_level", "get_probe_interval", "make_degrade_event",
    "MessageQueue", "QueueStats",
    "Scheduler", "SchedulerEvent",
    "HealthProbe",
    # HotFeed 热冷消息分级
    "AIMHotFeed", "HotFeedReport", "HotMessage",
    "DEFAULT_POLICY", "attach_to_client",
]
