"""
AIM 安全模型 v1.1 — 白名单 + 速率限制 + 认证链

认证链 Phase 1:
  - Step 1: 来源身份验证 (from_id 必须在注册 Agent 列表中)
  - Step 2: 速率限制 (令牌桶，每 Agent 独立)
  - Step 3: 白名单过滤 (可选的 sender 白名单)

Phase 2+ planned:
  - JWT token 签名验证
  - mTLS 双向认证
  - Scope/Claim 权限检查
  - 消息内容签名

用法:
    security = SecurityManager(config)
    # 在消息处理器中：
    if not security.authenticate(from_id, token=""):
        return  # 认证失败，丢弃
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Callable, Awaitable

logger = logging.getLogger("aim-client.security")


@dataclass
class TokenBucket:
    """令牌桶 — 每 Agent 独立限流"""
    rate: float          # 每秒补充 token 数
    burst: int           # 桶容量
    tokens: float = 0.0
    last_refill: float = 0.0

    def __post_init__(self):
        self.tokens = float(self.burst)
        self.last_refill = time.monotonic()

    def consume(self, n: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(float(self.burst), self.tokens + elapsed * self.rate)
        self.last_refill = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


class AuthStep(ABC):
    """认证链步骤基类"""
    @abstractmethod
    async def check(self, from_id: str, token: str = "", msg_id: str = "", envelope: dict | None = None) -> bool:
        ...


class SourceIdentityCheck(AuthStep):
    """来源身份验证：from_id 必须在注册 Agent 列表中

    从 NATS Operator users 目录读取注册 Agent 列表。
    回退：配置文件中的 registered_agents 列表。
    """
    def __init__(self, registered_agents: List[str]):
        self._registered = set(registered_agents)

    async def check(self, from_id: str, token: str = "", msg_id: str = "", envelope: dict | None = None) -> bool:
        if not self._registered:
            return True  # 无注册列表时默认放行（调试模式）
        ok = from_id in self._registered
        if not ok:
            logger.warning(f"🔒 来源身份验证失败: {from_id} 不在注册列表中")
        return ok

    def add_agent(self, agent_id: str):
        self._registered.add(agent_id)

    def remove_agent(self, agent_id: str):
        self._registered.discard(agent_id)


class RateLimitCheck(AuthStep):
    """速率限制：令牌桶算法"""
    def __init__(self, rate: float, burst: int):
        self._buckets: dict[str, TokenBucket] = {}
        self.rate = rate
        self.burst = burst

    async def check(self, from_id: str, token: str = "", msg_id: str = "", envelope: dict | None = None) -> bool:
        if from_id not in self._buckets:
            self._buckets[from_id] = TokenBucket(rate=self.rate, burst=self.burst)
        ok = self._buckets[from_id].consume()
        if not ok:
            logger.warning(f"⏱️ 限流: {from_id} (>{self.rate}/s)")
        return ok


class AllowlistCheck(AuthStep):
    """白名单过滤"""
    def __init__(self, allowlist: List[str], enabled: bool = False):
        self._allowlist = set(allowlist)
        self.enabled = enabled

    async def check(self, from_id: str, token: str = "", msg_id: str = "", envelope: dict | None = None) -> bool:
        if not self.enabled:
            return True
        ok = from_id in self._allowlist
        if not ok:
            logger.warning(f"🚫 白名单拒绝: {from_id}")
        return ok


class SecurityManager:
    """安全模型 v1.1 — 认证链

    链式执行：按序执行每个 AuthStep，任一步返回 False 则整链失败。
    调用方式：await security.authenticate(from_id, token, msg_id, envelope)
    """

    def __init__(self, config: dict):
        self.sec_config = config.get("security", {})
        self.auth_config = self.sec_config.get("auth", {})

        # 解析配置
        self.allowlist: list[str] = self.sec_config.get("allowlist", [])
        self.allowlist_enabled: bool = self.sec_config.get("allowlist_enabled", False)
        self.rate_enabled: bool = self.sec_config.get("rate_limit", {}).get("enabled", True)
        self.rate_max: int = self.sec_config.get("rate_limit", {}).get("max_per_second", 10)
        self.rate_burst: int = self.sec_config.get("rate_limit", {}).get("burst", 20)
        self.auth_mode: str = self.auth_config.get("mode", "chain")

        # 注册 Agent 列表
        self._registered_agents: list[str] = self.sec_config.get("registered_agents", [])

        # 构建认证链
        self._chain: List[AuthStep] = []
        self._build_chain()

        # 保留旧接口兼容
        self._buckets: dict[str, TokenBucket] = {}
        self._stats: dict[str, dict] = defaultdict(lambda: {"allowed": 0, "blocked": 0, "rate_limited": 0, "auth_failed": 0})

        logger.info(
            f"安全模型 v1.1 初始化: "
            f"chain_steps={len(self._chain)} "
            f"allowlist={'on' if self.allowlist_enabled else 'off'} "
            f"rate_limit={self.rate_max}/s "
            f"auth={self.auth_mode} "
            f"registered_agents={self._registered_agents}"
        )

    def _build_chain(self):
        """根据配置构建认证链"""
        chain_config = self.auth_config.get("chain", [])
        if chain_config:
            # 显式配置的链
            for step_name in chain_config:
                step = self._create_step(step_name)
                if step:
                    self._chain.append(step)
        else:
            # 默认链：来源验证 → 速率限制 → 白名单
            if self._registered_agents:
                self._chain.append(SourceIdentityCheck(self._registered_agents))
            if self.rate_enabled:
                self._chain.append(RateLimitCheck(self.rate_max, self.rate_burst))
            if self.allowlist_enabled:
                self._chain.append(AllowlistCheck(self.allowlist, enabled=True))

    def _create_step(self, name: str) -> Optional[AuthStep]:
        if name == "source_identity":
            return SourceIdentityCheck(self._registered_agents)
        elif name == "rate_limit":
            return RateLimitCheck(self.rate_max, self.rate_burst) if self.rate_enabled else None
        elif name == "allowlist":
            return AllowlistCheck(self.allowlist, enabled=True) if self.allowlist_enabled else None
        else:
            logger.warning(f"未知认证步骤: {name}")
            return None

    # ── 新接口：认证链 ──────────────────────────────────────

    async def authenticate(
        self,
        from_id: str,
        token: str = "",
        msg_id: str = "",
        envelope: dict | None = None,
    ) -> bool:
        """执行认证链 — 任意步骤失败则返回 False"""
        for step in self._chain:
            try:
                if not await step.check(from_id, token, msg_id, envelope):
                    self._stats[from_id]["auth_failed"] += 1
                    return False
            except Exception as e:
                logger.error(f"认证链步骤 {type(step).__name__} 异常: {e}")
                self._stats[from_id]["auth_failed"] += 1
                return False
        self._stats[from_id]["allowed"] += 1
        return True

    # ── 旧接口：保持兼容 ────────────────────────────────────

    def allow(self, from_id: str) -> bool:
        """白名单检查（旧接口）"""
        if not self.allowlist_enabled:
            return True
        allowed = from_id in self.allowlist
        if not allowed:
            self._stats[from_id]["blocked"] += 1
            logger.warning(f"🚫 白名单拒绝: {from_id}")
        return allowed

    def rate_ok(self, from_id: str) -> bool:
        """速率限制检查（旧接口）"""
        if not self.rate_enabled:
            return True
        if from_id not in self._buckets:
            self._buckets[from_id] = TokenBucket(rate=self.rate_max, burst=self.rate_burst)
        ok = self._buckets[from_id].consume()
        if not ok:
            self._stats[from_id]["rate_limited"] += 1
            logger.warning(f"⏱️ 限流: {from_id}")
        return ok

    # ── 管理接口 ────────────────────────────────────────────

    def register_agent(self, agent_id: str):
        """动态注册新 Agent（Registry 回调）"""
        self._registered_agents.append(agent_id)
        for step in self._chain:
            if isinstance(step, SourceIdentityCheck):
                step.add_agent(agent_id)
        logger.info(f"认证链注册新 Agent: {agent_id}")

    def stats(self) -> dict:
        return {
            "chain_steps": len(self._chain),
            "allowlist_enabled": self.allowlist_enabled,
            "rate_limit_enabled": self.rate_enabled,
            "auth_mode": self.auth_mode,
            "registered_agents": self._registered_agents,
            "per_agent": dict(self._stats),
        }

    def reset_stats(self):
        self._stats.clear()
