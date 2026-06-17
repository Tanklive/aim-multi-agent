#!/usr/bin/env python3
"""
call_adapter — AIM V3 adapter 调用封装

从 nats-agent-v3 独立出来，提供：
- 状态常量: SUCCESS / RETRY / DEGRADE / HUMAN / ERROR
- call_adapter() async 函数：调框架 adapter 处理消息
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

# ── 状态常量（与 adapter.sh 退出码对齐） ──────────────

SUCCESS = "success"      # exit 0 = 正常回复
RETRY   = "retry"        # exit 1 = 可重试
DEGRADE = "degrade"      # exit 2 = 降级/挂了
HUMAN   = "human"        # exit 3 = 需人工介入
ERROR   = "error"        # 异常（非标准退出码 / 超时 / OSError）

EXIT_CODE_MAP = {
    0: SUCCESS,
    1: RETRY,
    2: DEGRADE,
    3: HUMAN,
}

# ── 日志 ──────────────────────────────────────────────

log = logging.getLogger("aim-v3.call_adapter")

# ── 核心函数 ──────────────────────────────────────────


async def call_adapter(
    message: str,
    from_id: str,
    config: dict,
    timeout: float = None,
) -> dict:
    """调用框架 adapter 处理消息

    Args:
        message: 消息文本
        from_id: 发送方 Agent ID
        config: Agent 配置 (含 adapter_cmd, adapter_timeout)
        timeout: 超时秒数（默认从 config 读取，120s）

    Returns:
        {"status": SUCCESS|RETRY|DEGRADE|HUMAN|ERROR,
         "reply": "<回复文本>",
         "detail": "<详情>"}
    """
    adapter_cmd = config.get("adapter_cmd", "")
    if not adapter_cmd:
        return {
            "status": ERROR,
            "reply": "",
            "detail": "未配置 adapter_cmd",
        }

    timeout = timeout or config.get("adapter_timeout", 120.0)
    # 安全上限
    if isinstance(timeout, (int, float)):
        timeout = min(timeout, 300.0)

    # 转义消息中的特殊字符（shell safe）
    escaped_message = message.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    cmd = f'{adapter_cmd} process --message "{escaped_message}" --from "{from_id}"'

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "status": DEGRADE,
                "reply": "",
                "detail": f"Adapter 超时 ({timeout}s)",
            }

        exit_code = proc.returncode

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        # 映射退出码到状态
        status = EXIT_CODE_MAP.get(exit_code, ERROR if exit_code < 0 else DEGRADE)

        # 状态详情
        if exit_code not in EXIT_CODE_MAP:
            detail = f"非标准退出码 {exit_code}"
        elif stderr_text:
            detail = stderr_text[:200]
        else:
            detail = ""

        return {
            "status": status,
            "reply": stdout_text,
            "detail": detail,
        }

    except FileNotFoundError:
        return {
            "status": ERROR,
            "reply": "",
            "detail": f"adapter 命令不存在: {adapter_cmd}",
        }
    except Exception as e:
        return {
            "status": ERROR,
            "reply": "",
            "detail": f"adapter 调用异常: {e}",
        }
