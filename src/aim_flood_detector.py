#!/usr/bin/env python3
"""
AIM Flood/Loop Detector — 群聊循环检测

功能：
  1. 追踪群聊消息速率（per-sender）
  2. 检测确认循环（A↔B 相互回复纯确认 > 阈值）
  3. 触发时写 observer.jsonl 事件 → alertd 可感知

协议：
  - 只读：订阅 aim.grp.> 不发布
  - 静默：不参与群聊回复
  - 低资源：滑动窗口，~1MB 内存

Author: 呱呱 🐸 (ZS0001)
"""

import asyncio
import json
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

SDK_DIR = Path.home() / ".aim" / "bin"
sys.path.insert(0, str(SDK_DIR))
from aim_nats_sdk import AIMObserverClient

VERSION = "0.1.0"

# ── 配置 ──────────────────────────────────────────
FLOOD_WINDOW_SEC = 60        # 滑动窗口
FLOOD_THRESHOLD = 15         # 同 sender 窗口内 > N 消息 → 告警
LOOP_PAIR_THRESHOLD = 8      # 同对 AB↔BA 窗口内 > N → 确认循环
LOOP_CONTENT_MIN_LEN = 10    # 消息 <= 此长度才参与循环检测
OBSERVER_LOG = Path.home() / ".aim" / "system" / "observer.jsonl"


class FloodDetector:
    def __init__(self):
        # per-sender 消息时间戳
        self._sender_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=100)
        )
        # 配对计数器 {(A,B): count}
        self._pair_counter: dict[tuple, deque[float]] = defaultdict(
            lambda: deque(maxlen=100)
        )
        self._last_alert: dict[str, float] = {}  # 防重复告警 (type→last_ts)

    def feed(self, msg_id: str, from_id: str, to_id: str, content: str, ts: float):
        """处理一条群消息"""
        now = ts

        # 1. per-sender 速率
        self._sender_history[from_id].append(now)
        self._prune(self._sender_history[from_id], now)
        rate = len(self._sender_history[from_id])

        if rate > FLOOD_THRESHOLD:
            self._alert(f"FLOOD sender={from_id} rate={rate}/{FLOOD_WINDOW_SEC}s", now)

        # 2. 确认循环检测（短消息 + AB↔BA 互相快速回复）
        if len(content) <= LOOP_CONTENT_MIN_LEN:
            pair_key = tuple(sorted([from_id, to_id]))
            self._pair_counter[pair_key].append(now)
            self._prune(self._pair_counter[pair_key], now)
            pair_rate = len(self._pair_counter[pair_key])

            if pair_rate > LOOP_PAIR_THRESHOLD:
                self._alert(
                    f"LOOP pair={pair_key[0]}↔{pair_key[1]} "
                    f"rate={pair_rate}/{FLOOD_WINDOW_SEC}s",
                    now,
                )

    def _prune(self, dq: deque, now: float):
        """清理窗口外的时间戳"""
        cutoff = now - FLOOD_WINDOW_SEC
        while dq and dq[0] < cutoff:
            dq.popleft()

    def _alert(self, msg: str, now: float):
        """写告警到 observer.jsonl"""
        # 同类型告警 5 分钟内不重复
        alert_type = msg.split()[0]
        last = self._last_alert.get(alert_type, 0)
        if now - last < 300:
            return
        self._last_alert[alert_type] = now

        entry = {
            "agent_id": "aim-watch",
            "status": "flood",
            "msg_id": "fd-" + str(int(now)),
            "detail": msg,
            "ts": now,
        }
        try:
            OBSERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(OBSERVER_LOG, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(
                f"[flood-detector] {datetime.now().strftime('%H:%M:%S')} ALERT: {msg}",
                flush=True,
            )
        except Exception:
            pass


async def main():
    print(f"[flood-detector v{VERSION}] 启动中…")
    detector = FloodDetector()

    client = AIMObserverClient(agent_id="aim-watch")
    await client.connect()

    async def on_grp(msg):
        try:
            data = json.loads(msg.data) if isinstance(msg.data, bytes) else msg.data
            content = data.get("payload", {}).get("text", "") or data.get("content", "")
            mid = data.get("id", "") or data.get("msg_id", "")
            frm = data.get("from", "") or data.get("from_id", "")
            grp = data.get("grp_id", "") or data.get("to_id", "")
            if content:
                detector.feed(mid, frm, grp, content, time.time())
        except Exception:
            pass

    await client.subscribe_grp("grp_trio", on_grp)

    # 维持运行
    while True:
        await asyncio.sleep(30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[flood-detector] 已停止")
