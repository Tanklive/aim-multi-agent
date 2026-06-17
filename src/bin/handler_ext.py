#!/usr/bin/env python3
"""handler_ext.py — handler.sh 的事件发射器 v1.0

让 handler.sh（shell 脚本）可以发射 Observer 事件。
handler.sh 在处理消息的关键节点通过管道调用此脚本。

用法（handler.sh 内）：
  echo "received|msg_id|收到来自 ZS0001 的消息" | python3 handler_ext.py
  echo "ai_start|msg_id|调用 AI 框架" | python3 handler_ext.py
  echo "ai_done|msg_id|AI 回复: ..." | python3 handler_ext.py
  echo "ai_empty|msg_id|AI 未生成回复" | python3 handler_ext.py
  echo "completed|msg_id|已回复" | python3 handler_ext.py
  echo "error|msg_id|出错信息" | python3 handler_ext.py

环境变量（由 nats-agent.py 注入）：
  AIM_AGENT_ID  — 本 Agent ID（如 ZS0003）

依赖：
  - ~/.aim/bin/aim_nats_sdk.py（SDK）
  - NATS 连接（首次调用时自动建立）
"""

import asyncio
import json
import os
import sys
from pathlib import Path

SDK_DIR = Path.home() / ".aim" / "bin"
if str(SDK_DIR) not in sys.path:
    sys.path.insert(0, str(SDK_DIR))

from aim_nats_sdk import AIMNATSClient

_client = None


async def _ensure_client():
    """延迟初始化 NATS 客户端（首次调用时连接）"""
    global _client
    if _client is None:
        agent_id = os.environ.get("AIM_AGENT_ID", "ZS0003")
        _client = AIMNATSClient.from_config(agent_id)
        await _client.connect()
        # 不需要 setup_streams — emit_obs 会自动处理
    return _client


async def emit(status: str, msg_id: str, detail: str):
    """发射一条 Observer 事件"""
    client = await _ensure_client()
    await client.emit_obs(status, msg_id, detail)


if __name__ == "__main__":
    line = sys.stdin.read().strip()
    if not line:
        sys.exit(0)

    parts = line.split("|", 2)
    if len(parts) == 3:
        asyncio.run(emit(parts[0], parts[1], parts[2]))
    else:
        # 忽略格式错误，不崩溃
        pass
