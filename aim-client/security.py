"""
AIM 安全模型 v1 — 白名单 + 速率限制 + 认证链

Phase 1 实现:
  - Allowlist: 消息来源白名单过滤（Transport 层拦截）
  - RateLimiter: 令牌桶算法，每 Agent 每秒 N 条（默认10）
  - AuthChain: JWT 认证链（Phase 1 默认放行，P2+ mTLS/OAuth2）

用法:
    security = SecurityManager(config)
    security.load_config()

    # Transport 层：
    if not security.allow("ZS0002"):
        return  # 丢弃

    if not security.rate_ok("ZS0002"):
        return  # 限流

    # 认证链（P1 简化版）
    if not security.authenticate(token):
        return

配置 (config.json):
    {
      "security": {
        "allowlist": ["ZS0001", "ZS0002", "ZS0003"],
        "allowlist_enabled": false,
        "rate_limit": {
          "enabled": true,
          "max_per_second": 10,
          "burst": 20
        },
        "auth": {
          "mode": "jwt",
          "jwt_secret": ""
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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


class SecurityManager:
    """安全模型 v1"""

    def __init__(self, config: dict):
        self.sec_config = config.get("security", {})
        self.allowlist: list[str] = self.sec_config.get("allowlist", [])
        self.allowlist_enabled: bool = self.sec_config.get("allowlist_enabled", False)
        self.rate_enabled: bool = self.sec_config.get("rate_limit", {}).get("enabled", True)
        self.rate_max: int = self.sec_config.get("rate_limit", {}).get("max_per_second", 10)
        self.rate_burst: int = self.sec_config.get("rate_limit", {}).get("burst", 20)
        self.auth_mode: str = self.sec_config.get("auth", {}).get("mode", "jwt")

        # 每个发件人独立令牌桶
        self._buckets: dict[str, TokenBucket] = {}
        self._stats: dict[str, dict] = defaultdict(lambda: {"allowed": 0, "blocked": 0, "rate_limited": 0})

        logger.info(f"安全模型 v1 初始化: allowlist={'on' if self.allowlist_enabled else 'off'} "
                     f"rate_limit={self.rate_max}/s auth={self.auth_mode}")

    def allow(self, from_id: str) -> bool:
        """白名单检查 — Transport 层入口"""
        if not self.allowlist_enabled:
            return True
        allowed = from_id in self.allowlist
        if not allowed:
            self._stats[from_id]["blocked"] += 1
            logger.warning(f"🚫 白名单拒绝: {from_id}")
        else:
            self._stats[from_id]["allowed"] += 1
        return allowed

    def rate_ok(self, from_id: str) -> bool:
        """速率限制检查 — 令牌桶"""
        if not self.rate_enabled:
            return True
        if from_id not in self._buckets:
            self._buckets[from_id] = TokenBucket(rate=self.rate_max, burst=self.rate_burst)
        ok = self._buckets[from_id].consume()
        if not ok:
            self._stats[from_id]["rate_limited"] += 1
            logger.warning(f"⏱️ 限流: {from_id}")
        return ok

    def authenticate(self, token: str = "") -> bool:
        """认证链 — Phase 1: JWT 占位"""
        if self.auth_mode == "none":
            return True
        # Phase 1: JWT 默认放行（NATS creds 已在 Transport/SDK 层处理）
        # Phase 2+: 验证 JWT token 签名/过期/claims
        return True

    def stats(self) -> dict:
        """获取安全统计"""
        return {
            "allowlist_enabled": self.allowlist_enabled,
            "rate_limit_enabled": self.rate_enabled,
            "auth_mode": self.auth_mode,
            "per_agent": dict(self._stats),
        }

    def reset_stats(self):
        """重置统计计数器"""
        self._stats.clear()
