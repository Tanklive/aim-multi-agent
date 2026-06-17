"""
AIM Observer — NATS 版 (精简版)
订阅 NATS Observer 事件，实时展示

原版 446 行 → 精简版 ~100 行
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from typing import Optional

try:
    import nats
except ImportError:
    print("ERROR: pip install nats-py")
    sys.exit(1)


class AIMObserver:
    """AIM Observer — NATS 订阅"""

    def __init__(self, agent_id: str, watch_target: str,
                 nats_url: str = "nats://127.0.0.1:4222", verbose: bool = False):
        self.agent_id = agent_id
        self.watch_target = watch_target
        self.nats_url = nats_url
        self.verbose = verbose
        self.nc = None
        self._running = False

    async def connect(self):
        """连接到 NATS 并订阅 Observer 事件"""
        try:
            print(f"🔗 连接 {self.nats_url} ...")
            self.nc = await nats.connect(self.nats_url, ping_interval=20)
            print(f"✅ 已连接，watching {self.watch_target}")
            print("─" * 60)

            # 订阅 Observer 事件
            await self.nc.subscribe("observer.events.>", cb=self._on_event)
            print(f"📡 已订阅 observer.events.>")

            self._running = True
            while self._running:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            print("\n⏹ 停止")
        except Exception as e:
            print(f"❌ 连接失败: {e}")
        finally:
            if self.nc:
                await self.nc.close()

    async def _on_event(self, msg):
        """处理 Observer 事件"""
        try:
            data = json.loads(msg.data)
            event_type = data.get("type", "?")
            agent_id = data.get("agent_id", "?")
            detail = data.get("detail", "")
            ts = data.get("ts", 0)
            dt = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "??:??:??"

            # 过滤：只显示 watch_target 的事件（或全部）
            if self.watch_target != "all" and agent_id != self.watch_target:
                return

            # 状态图标
            icons = {
                "agent_online": "🟢",
                "agent_offline": "🔴",
                "heartbeat": "💓",
                "message": "💬",
                "error": "❌",
            }
            icon = icons.get(event_type, "📢")

            print(f"[{dt}] {icon} {event_type}: {agent_id} — {detail[:100]}")

            if self.verbose and detail:
                print(f"         📝 {detail}")

        except json.JSONDecodeError:
            pass

    def stop(self):
        self._running = False


async def run_observer(agent_id: str, watch_target: str,
                       nats_url: str = "nats://127.0.0.1:4222", verbose: bool = False):
    """运行 Observer"""
    observer = AIMObserver(agent_id, watch_target, nats_url, verbose)
    await observer.connect()


def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="AIM Observer — NATS 版")
    parser.add_argument("target", help="要 watch 的目标 agent_id (或 all)")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222", help="NATS Server URL")
    parser.add_argument("--agent-id", default="observer", help="observer 的 agent_id")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")

    args = parser.parse_args()

    print(f"👀 AIM Observer — watching {args.target}")
    print(f"📡 NATS: {args.nats_url}")
    print("─" * 60)

    asyncio.run(run_observer(args.agent_id, args.target, args.nats_url, args.verbose))


if __name__ == "__main__":
    main()
