"""
AIM Client Modules

模块化设计 — 每个子模块独立封装一种能力，通过 Transport 注入。
所有模块支持独立测试，不依赖 main.py 的全局状态。

模块列表：
    ChatArchive  — 聊天记录持久化（JSONL + 分页查询）
"""

__all__ = ["ChatArchive"]

from .chat_archive import ChatArchive
