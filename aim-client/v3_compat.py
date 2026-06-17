"""
V3 兼容模式 — nats-agent-v3 降级包装器

Phase 1: aim-client 替代 nats-agent-v3 作为标准入口。
在迁移过渡期，--mode legacy 通过此模块启动 nats-agent-v3 作为兼容子进程。

生命周期:
  aim-client (--mode legacy)
    └── V3Compat.start() → subprocess: nats-agent-v3.py
    └── V3Compat.health_check() → 探活 + 自动重启
    └── V3Compat.stop() → 优雅关闭

Phase 2: 此模块移除，aim-client 完全替代 V3。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aim-client.v3compat")

V3_PATH = Path.home() / "shared" / "aim" / "nats-agent-v3" / "nats-agent-v3.py"


class V3Compat:
    """nats-agent-v3 兼容包装器"""

    def __init__(self, agent_id: str, config_path: str, nats_url: str = ""):
        self.agent_id = agent_id
        self.config_path = config_path
        self.nats_url = nats_url
        self._proc: Optional[asyncio.subprocess.Process] = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> bool:
        """启动 nats-agent-v3 子进程"""
        if not V3_PATH.exists():
            logger.error(f"V3 兼容: nats-agent-v3 不存在 ({V3_PATH})")
            return False

        cmd = [
            sys.executable, str(V3_PATH),
            "--agent-id", self.agent_id,
            "--config", self.config_path,
        ]
        if self.nats_url:
            cmd.extend(["--nats-url", self.nats_url])

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(f"V3 兼容启动: {self.agent_id} (PID={self._proc.pid})")

            # 等待 V3 启动（等它日志输出 "已连接到 NATS"）
            for _ in range(30):  # 30s 超时
                line = await self._read_line()
                if line and "已连接到 NATS" in line:
                    logger.info(f"V3 {self.agent_id} 已就绪")
                    return True
                await asyncio.sleep(0.5)
            
            logger.warning("V3 启动超时(30s)")
            return self.is_running

        except Exception as e:
            logger.error(f"V3 启动失败: {e}")
            return False

    async def health_check(self) -> bool:
        """探活检查 — 进程存活则返回 True"""
        if not self._proc:
            return False
        if self._proc.returncode is not None:
            logger.warning(f"V3 {self.agent_id} 退出 (code={self._proc.returncode})")
            return False
        # 尝试发 SIGWINCH (无操作信号，检测进程是否响应)
        try:
            os.kill(self._proc.pid, signal.SIGWINCH)
            return True
        except ProcessLookupError:
            return False
        except Exception:
            return self.is_running

    async def stop(self):
        """优雅关闭 V3"""
        if self._proc and self._proc.returncode is None:
            logger.info(f"停止 V3 {self.agent_id} (PID={self._proc.pid})")
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
            logger.info(f"V3 {self.agent_id} 已停止")

    async def _read_line(self) -> Optional[str]:
        """非阻塞读一行 stderr"""
        if not self._proc or not self._proc.stderr:
            return None
        try:
            line = await asyncio.wait_for(
                self._proc.stderr.readline(), timeout=0.5
            )
            return line.decode().strip() if line else None
        except (asyncio.TimeoutError, Exception):
            return None


async def run_v3_mode(agent_id: str, config_path: str, nats_url: str = ""):
    """--mode legacy 入口：启动 V3 兼容 + 探活保活"""
    v3 = V3Compat(agent_id, config_path, nats_url)

    if not await v3.start():
        logger.error("V3 模式启动失败")
        return

    health_failures = 0
    try:
        while True:
            await asyncio.sleep(5)
            if await v3.health_check():
                health_failures = 0
            else:
                health_failures += 1
                logger.warning(f"V3 不健康 ({health_failures}/3)")
                if health_failures >= 3:
                    logger.info("V3 重启中...")
                    await v3.stop()
                    if not await v3.start():
                        logger.error("V3 重启失败")
                        break
                    health_failures = 0
    except KeyboardInterrupt:
        await v3.stop()
