"""
AIM NATS 模块
NATS 替代 WebSocket 的全新架构实现
"""

from .client import AIMNATSClient, AIMMessage, Subjects
from .retry_manager import RetryManager, RetryPolicy

__version__ = "0.1.0"
__all__ = ["AIMNATSClient", "AIMMessage", "Subjects", "RetryManager", "RetryPolicy"]