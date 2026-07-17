"""
AIM Client Modules

模块化设计 — 每个子模块独立封装一种能力，通过 Transport 注入。
所有模块支持独立测试，不依赖 main.py 的全局状态。

模块列表：
    ChatArchive  — 聊天记录持久化（JSONL + 分页查询）
    GroupManager — 群管理（创建/加入/退出/审批/查询），统一 NATS 通信 + 意图识别
"""

__all__ = ["ChatArchive", "GroupManager"]

from .chat_archive import ChatArchive
from .group_manager import GroupManager
