"""HealthProbe — 通过 adapter.sh health 探测 Runtime 状态

Phase 0 实现：subprocess 调 adapter.sh health，返回 StateReport。
Phase 1 会集成到 Monitor 模块。
"""
from __future__ import annotations
import asyncio
import json
import logging
import time

from .types import AgentState, StateReport

logger = logging.getLogger(__name__)


class HealthProbe:
    """Runtime 健康探针

    通过 adapter.sh health 标准化接口探测 Runtime 状态：
      exit 0 + stdout JSON → 健康
      exit 1 → 降级（框架忙）
      exit 2 → 挂了
    """

    def __init__(
        self,
        health_cmd: str,
        timeout: float = 10.0,
        env: dict | None = None,
    ):
        self.health_cmd = health_cmd  # e.g. "adapter.sh health"
        self.timeout = timeout
        self.env = env
        self._degraded_count = 0

    async def probe(self) -> StateReport:
        """执行一次探针，返回 StateReport"""
        try:
            proc = await asyncio.create_subprocess_shell(
                self.health_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("HealthProbe 超时")
                self._degraded_count += 1
                return StateReport(
                    status=AgentState.OFFLINE,
                    last_heartbeat=time.time(),
                )

            exit_code = proc.returncode if proc.returncode is not None else -1

            if exit_code == 0:
                # 健康
                self._degraded_count = 0
                info = {}
                try:
                    info = json.loads(stdout.decode().strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

                return StateReport(
                    status=AgentState.IDLE,
                    active_sessions=info.get("active_sessions", 0),
                    avg_latency_ms=info.get("avg_latency_ms", 0),
                    last_heartbeat=time.time(),
                )

            elif exit_code == 1:
                # 降级（框架忙）
                self._degraded_count += 1
                logger.debug(f"HealthProbe: degraded (count={self._degraded_count})")
                return StateReport(
                    status=AgentState.BUSY,
                    active_sessions=1,
                    last_heartbeat=time.time(),
                )

            else:
                # 挂了
                self._degraded_count += 1
                stderr_text = stderr.decode()[:200] if stderr else "未知"
                logger.warning(f"HealthProbe: unhealthy (exit={exit_code}): {stderr_text}")
                return StateReport(
                    status=AgentState.OFFLINE,
                    last_heartbeat=time.time(),
                )

        except FileNotFoundError:
            logger.error(f"HealthProbe: 命令不存在: {self.health_cmd}")
            return StateReport(status=AgentState.OFFLINE, last_heartbeat=time.time())
        except Exception as e:
            logger.error(f"HealthProbe 异常: {e}")
            return StateReport(status=AgentState.OFFLINE, last_heartbeat=time.time())
