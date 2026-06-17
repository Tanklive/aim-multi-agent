"""
AI 数据类型定义

Phase 2: 统一请求/响应结构，为 CLIAdapter 基类提供类型支持

Author: 呱呱 🐸 | Review: 吉量 🐴
"""

from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class AIRequest:
    """AI 请求数据类

    统一所有框架的请求格式，替代直接传 prompt 字符串。
    不做 messages 多轮格式 — 保持简单，单轮 prompt 即可。
    """
    prompt: str                                    # 用户消息/提示词
    timeout: int = 120                             # 超时秒数
    session_id: Optional[str] = None               # 会话 ID（有则恢复会话）
    session_key: Optional[str] = None              # 会话 Key（openclaw 专用）
    agent_id: Optional[str] = None                 # Agent ID
    sender: Optional[str] = None                   # 消息发送者
    priority: str = "medium"                       # 优先级: high/medium/low
    context: Optional[str] = None                  # 上下文（最近对话记录）
    metadata: dict = field(default_factory=dict)   # 扩展元数据

    @property
    def message_length(self) -> int:
        """消息长度（用于动态超时计算）"""
        return len(self.prompt)

    def effective_timeout(self) -> int:
        """计算有效超时时间（含动态调整）"""
        timeout = self.timeout
        if self.priority == "high":
            timeout = max(timeout, 180)
        elif self.priority == "low":
            timeout = max(timeout, 300)

        # 动态超时：长消息需要更多处理时间
        msg_len = self.message_length
        if msg_len > 1000:
            timeout = max(timeout, 300)
        elif msg_len > 500:
            timeout = max(timeout, 180)

        return timeout


@dataclass
class AIResponse:
    """AI 响应数据类

    统一所有框架的响应格式，替代 dict 返回值。
    """
    success: bool                                  # 是否成功
    text: str = ""                                 # 回复文本
    session_id: Optional[str] = None               # 提取到的会话 ID
    error: Optional[str] = None                    # 错误信息
    latency_ms: Optional[int] = None               # 调用耗时（毫秒）
    metadata: dict = field(default_factory=dict)   # 扩展元数据

    @classmethod
    def from_dict(cls, data: dict) -> "AIResponse":
        """从旧格式 dict 转换（向后兼容）"""
        return cls(
            success=data.get("success", False),
            text=data.get("text", ""),
            session_id=data.get("session_id"),
            error=data.get("error"),
            latency_ms=data.get("latency_ms"),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict:
        """转为 dict（向后兼容旧代码）"""
        result = {
            "success": self.success,
            "text": self.text,
        }
        if self.session_id:
            result["session_id"] = self.session_id
        if self.error:
            result["error"] = self.error
        if self.latency_ms is not None:
            result["latency_ms"] = self.latency_ms
        if self.metadata:
            result["metadata"] = self.metadata
        return result
