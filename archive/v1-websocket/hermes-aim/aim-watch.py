#!/usr/bin/env python3
"""
AIM Watch — 实时消息监控窗口（只读）

显示 AIM 群聊和私聊的消息收发、AI 处理过程。
默认看全部消息，--watch 指定只看某个 Agent。

用法:
  python3 aim-watch.py                              # 看全部消息（默认 watch_target="*"）
  python3 aim-watch.py --watch ZS0001               # 只看呱呱
  python3 aim-watch.py --watch ZS0005               # 只看小火鸡儿
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("ERROR: pip install websockets")
    sys.exit(1)

# 路径设置
AIM_DIR = Path(__file__).parent
sys.path.insert(0, str(AIM_DIR))


def build_observer_auth(agent_id: str, watch_target: str = "") -> dict:
    """构建 observer 认证载荷"""
    from security import get_security_manager
    sec = get_security_manager()
    payload = sec.build_auth_payload(agent_id)
    payload["channel"] = "observer"
    payload["verbose"] = True
    if watch_target:
        payload["watch_target"] = watch_target
    return payload


def fmt_time(ts: float = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%H:%M:%S")


class AIMWatch:
    """AIM Watch 客户端"""

    def __init__(self, server: str, auth: dict, watch_filter: str = ""):
        self.server = server
        self.auth = auth
        self.watch_filter = watch_filter
        self._ws = None
        self._running = False
        self._task_timers: dict = {}

    async def run(self):
        self._running = True
        while self._running:
            try:
                async with ws_connect(self.server, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    print(f"🔗 连接 {self.server} ...")

                    await ws.send(json.dumps(self.auth))
                    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if resp.get("cmd") != "auth_ok":
                        print(f"❌ 认证失败: {resp.get('reason', '')}")
                        await asyncio.sleep(5)
                        continue

                    flt = f" (只看: {self.watch_filter})" if self.watch_filter else " (全部消息)"
                    print(f"✅ aim-watch 已连接{flt}")
                    print("─" * 80)

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            await self._handle(msg)
                        except json.JSONDecodeError:
                            continue

            except (ConnectionRefusedError, OSError) as e:
                print(f"\n⚠️ 连接失败: {e}，5秒后重连...")
                await asyncio.sleep(5)
            except websockets.ConnectionClosed:
                print(f"\n⚠️ 连接断开，重连中...")
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"\n❌ 异常: {e}")
                await asyncio.sleep(5)

    async def _handle(self, msg: dict):
        cmd = msg.get("cmd") or msg.get("msg_type")

        if cmd == "message":
            self._display_message(msg)
        elif cmd == "status_feedback":
            self._display_status_feedback(msg)
        elif cmd in ("heartbeat", "presence", "lifecycle", "ack", "status_update", "heartbeat_ack"):
            pass  # 静默

    def _should_show(self, from_agent: str) -> bool:
        if not self.watch_filter:
            return True
        return from_agent == self.watch_filter

    def _display_message(self, msg: dict):
        msg_data = msg.get("msg", msg)
        from_agent = msg_data.get("from_id", msg_data.get("from", "?"))
        content = msg_data.get("content", "")
        target = msg_data.get("to", "")
        is_group = msg_data.get("group", False)
        ts = msg_data.get("timestamp", msg.get("timestamp", time.time()))

        if not self._should_show(from_agent):
            return

        tag = "📢群" if is_group else "📨私"
        prefix = f"[{fmt_time(ts)}]"

        # 完整显示，不截断
        for line in content.split("\n"):
            print(f"{prefix} {tag} {from_agent} → {target}  {line}")
        print()

    def _display_status_feedback(self, msg: dict):
        step = msg.get("step", "?")
        status = msg.get("status", "?")
        progress = msg.get("progress", "")
        session_id = msg.get("session_id", "?")
        from_agent = msg.get("from", "?")
        content = msg.get("content", "")
        ts = msg.get("timestamp", time.time())

        if not self._should_show(from_agent):
            return

        icon = {"running": "🟡", "completed": "✅", "error": "❌",
                "timeout": "⏰", "interrupted": "🔌"}.get(status, "❓")

        if step == "task_start" and status == "running":
            self._task_timers[session_id] = time.time()

        dur = ""
        if step in ("task_end",) and session_id in self._task_timers:
            start = self._task_timers.pop(session_id, None)
            if start:
                elapsed = time.time() - start
                dur = f" ⏱ {elapsed:.1f}s" if elapsed >= 1 else f" ⏱ {elapsed*1000:.0f}ms"

        short_id = session_id[-8:] if len(session_id) > 8 else session_id

        if step == "task_start":
            print(f"[{fmt_time(ts)}] {icon} [{short_id}] {from_agent} 开始处理 ⏱ 0ms")
            if content:
                print(f"   📨 收到: {content}")
        elif step == "task_end":
            print(f"[{fmt_time(ts)}] {icon} [{short_id}] {from_agent} 处理完成{dur}")
            if status == "completed" and content:
                print(f"   💬 回复: {content}")
            elif status == "error":
                print(f"   ❌ 错误: {progress or '未知错误'}")
        print()


def main():
    parser = argparse.ArgumentParser(description="AIM Watch — 实时消息监控")
    parser.add_argument("--server", default="ws://127.0.0.1:18900",
                        help="AIM Server 地址")
    parser.add_argument("--agent-id", default="observer",
                        help="observer 身份")
    parser.add_argument("--watch", default="*",
                        help="只看某个 Agent（如 ZS0001），默认 \"*\" 看全部")

    args = parser.parse_args()
    auth = build_observer_auth(args.agent_id, args.watch)

    watch = AIMWatch(args.server, auth, args.watch)
    try:
        asyncio.run(watch.run())
    except KeyboardInterrupt:
        print("\n👋 aim-watch 已停止")


if __name__ == "__main__":
    main()
