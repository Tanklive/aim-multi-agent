"""
CLIAdapter — CLI 适配器抽象基类

所有框架适配器的统一接口。FrameworkCLI 继承此类。

Phase 2 设计决策（吉量确认）：
- 基类名：CLIAdapter（不用 ABC，留给以后扩展）
- 请求/响应：AIRequest(prompt:str) / AIResponse
- 方法名：call()（不叫 chat()）

Author: 呱呱 🐸 | Review: 吉量 🐴
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from ai_types import AIRequest, AIResponse

log = logging.getLogger("aim.cli_adapter")


class CLIAdapter(ABC):
    """CLI 适配器抽象基类

    所有框架（hermes/openclaw/qwenpaw/crewai/...）的统一接口。
    子类必须实现 call() 方法。

    用法:
        adapter: CLIAdapter = FrameworkCLI("hermes", commands, cli_paths)
        request = AIRequest(prompt="你好", timeout=120)
        response = await adapter.call(request)
        if response.success:
            print(response.text)
    """

    @abstractmethod
    async def call(self, request: AIRequest) -> AIResponse:
        """统一调用接口

        Args:
            request: AIRequest 数据类，包含 prompt、timeout、session_id 等

        Returns:
            AIResponse 数据类，包含 success、text、error 等
        """
        pass

    def health_check(self) -> bool:
        """健康检查（可选覆写）

        默认实现：尝试调用一个简单请求来检测可用性。
        子类可覆写为更高效的检查方式（如 HTTP ping）。

        Returns:
            True = 健康, False = 不可用
        """
        return True

    @property
    def framework_name(self) -> str:
        """框架名称（子类应覆写）"""
        return "unknown"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} framework={self.framework_name}>"
