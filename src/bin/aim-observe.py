#!/usr/bin/env python3
"""
AIM Observer — NATS 版终端监控（基于 SDK AIMObserverClient）

基于 ~/aim-server/aim-observe.py（呱呱原型）重新开发的公网标准版。

用法:
  aim-observe                      # 看全部 Agent 状态
  aim-observe --agent ZS0001      # 只看呱呱
  aim-observe --history 10        # 回放最近 10 条
  aim-observe --json              # JSON 输出（机器可读）

安全:
  - 只读连接（只订阅 aim.obs.>，不发布）
  - 支持 Token / NKEY-JWT 认证
  - 断线自动重连
"""

import asyncio
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# SDK 路径
SDK_DIR = Path.home() / ".aim" / "bin"
if str(SDK_DIR) not in sys.path:
    sys.path.insert(0, str(SDK_DIR))

from aim_nats_sdk import AIMObserverClient

# ── 状态图标映射 ─────────────────────────────────────
STATUS_ICONS = {
    "processing": "🟡",
    "completed": "✅",
    "error": "❌",
    "heartbeat": "💓",
    "online": "🟢",
    "offline": "🔴",
    "timeout": "⏰",
    "interrupted": "🔌",
}


def fmt_time(ts: float) -> str:
    if ts <= 0:
        return "??:??:??"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


async def show_history(obs: AIMObserverClient, args):
    """回放历史并退出"""
    now = datetime.now().timestamp()
    start = now - 3600 if args.history == -1 else now - args.history * 60

    events = await obs.get_history(
        agent_filter=args.agent,
        start_time=start,
        page=1,
        page_size=50,
    )

    if not events:
        print("📭 没有找到历史事件")
        return

    for event in events:
        _display_event(event, args.json)

    print(f"\n📜 共 {len(events)} 条历史事件")


def _display_event(event: dict, json_mode: bool = False):
    """显示一条 Observer 事件"""
    if json_mode:
        print(json.dumps(event, ensure_ascii=False))
        return

    agent_id = event.get("agent_id", "???")
    status = event.get("status", "?")
    detail = event.get("detail", "")
    msg_id = event.get("msg_id", "")
    ts = event.get("ts", 0)
    time_str = fmt_time(ts)
    icon = STATUS_ICONS.get(status, "📢")

    if not agent_id or agent_id == "???":
        return

    line = f"[{time_str}] {icon} {agent_id}: {status}"
    if detail:
        line += f" — {detail[:120]}"
    print(line)

    if msg_id and os.environ.get("AIM_OBS_VERBOSE"):
        print(f"         msg_id: {msg_id}")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="AIM Observer — NATS 版")
    parser.add_argument("--agent", default=">", help="只看某个 Agent（如 ZS0001），默认全部")
    parser.add_argument("--history", type=int, default=0,
                        help="回放最近 N 分钟的历史（-1 回放最近 1 小时）")
    parser.add_argument("--json", action="store_true", help="JSON 输出模式")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222", help="NATS Server URL")
    args = parser.parse_args()

    obs = AIMObserverClient.from_config(
        observer_id="cli-observe",
        server=args.nats_url,
    )

    await obs.connect()

    if args.history != 0:
        await show_history(obs, args)
        await obs.disconnect()
        return

    if not args.json:
        print(f"👀 AIM Observer 已启动 (NATS 版)")
        print(f"📡 NATS: {args.nats_url}")
        flt = f"只看 {args.agent}" if args.agent != ">" else "全部 Agent"
        print(f"🎯 目标: {flt}")
        print("─" * 60)

    # 订阅实时事件
    async def on_event(event: dict):
        _display_event(event, args.json)

    await obs.subscribe(on_event, agent_filter=args.agent)

    # 永久运行
    try:
        await obs.wait_forever()
    except KeyboardInterrupt:
        pass

    if not args.json:
        print("\n👋 Observer 已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
