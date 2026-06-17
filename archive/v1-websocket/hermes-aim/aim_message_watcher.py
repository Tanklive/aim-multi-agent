#!/usr/bin/env python3
"""
AIM 消息 Watcher — 防御性兜底

作用：定期扫描 messages.jsonl，发现发给当前 Agent 但 aim-agent 未处理的消息，
      通过 message_bridge 推送到桥接文件给主会话感知。

适用于：WS 推送 + DeliveryGuarantee 双重失效时捡漏。

启动方式：launchd 管理，或手动 python3 aim_message_watcher.py
"""

import json
import os
import sys
import time
import logging
from datetime import datetime

# ── 配置 ──

MY_AGENT_ID = "ZS0002"
MESSAGES_PATH = os.path.expanduser("~/.hermes/aim/data/messages.jsonl")
STATE_FILE = os.path.expanduser("~/.hermes/aim/data/watcher_state.json")
SCAN_INTERVAL = 30  # 轮询间隔（秒）

# 导入 message_bridge
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import message_bridge
    bridge_available = True
except ImportError:
    bridge_available = False

# ── 日志 ──

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watcher] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aim.watcher")


# ── 状态管理（记录已处理的消息，避免重复推送） ──

class WatcherState:
    def __init__(self, state_file: str):
        self.state_file = state_file
        self._seen_msgs: set = set()
        self._last_scan_ts: float = 0
        self._load()

    def _load(self):
        """从文件加载持久化状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    data = json.load(f)
                    self._seen_msgs = set(data.get("seen_msgs", []))
                    self._last_scan_ts = data.get("last_scan_ts", 0)
                log.info(f"已加载状态: {len(self._seen_msgs)} 条已处理, 上次扫描 {self._last_scan_ts}")
            except (json.JSONDecodeError, Exception) as e:
                log.warning(f"状态文件加载失败: {e}，从头开始")

    def save(self):
        """持久化到文件"""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            data = {
                "seen_msgs": list(self._seen_msgs),
                "last_scan_ts": self._last_scan_ts,
                "updated_at": time.time(),
            }
            with open(self.state_file, "w") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            log.error(f"状态保存失败: {e}")

    def is_seen(self, msg_id: str) -> bool:
        return msg_id in self._seen_msgs

    def mark_seen(self, msg_id: str):
        self._seen_msgs.add(msg_id)
        # 控制内存上限（保留最近 2000 条）
        if len(self._seen_msgs) > 2000:
            # 移除最早的 500 条
            self._seen_msgs = set(list(self._seen_msgs)[-1500:])

    def update_scan_ts(self, ts: float):
        self._last_scan_ts = ts


# ── 扫描核心 ──

def scan_messages(state: WatcherState) -> list:
    """扫描 messages.jsonl，返回未处理的新消息"""
    if not os.path.exists(MESSAGES_PATH):
        return []

    new_msgs = []
    try:
        with open(MESSAGES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_id = msg.get("msg_id", "")
                to = msg.get("to", "")
                from_id = msg.get("from", "")

                # 只处理发给我的消息
                if to != MY_AGENT_ID:
                    continue

                # 不处理自己的消息
                if from_id == MY_AGENT_ID:
                    continue

                # 检查是否已处理过
                if state.is_seen(msg_id):
                    continue

                content = msg.get("content", "")
                ts = msg.get("ts", msg.get("time", 0))
                is_group = msg.get("group", False)

                # 群聊消息也要处理
                if is_group:
                    log.debug(f"发现群聊消息: {msg_id} from={from_id}")
                    # 群聊消息走群聊处理逻辑（暂不处理）
                    state.mark_seen(msg_id)
                    continue

                new_msgs.append({
                    "msg_id": msg_id,
                    "from": from_id,
                    "content": content,
                    "ts": ts,
                })
                state.mark_seen(msg_id)

                # 更新扫描时间戳
                if isinstance(ts, (int, float)) and ts > state._last_scan_ts:
                    state.update_scan_ts(ts)

    except Exception as e:
        log.error(f"扫描失败: {e}")

    return new_msgs


def push_to_bridge(new_msgs: list):
    """将新消息推送到桥接文件"""
    global bridge_available
    if not bridge_available:
        log.warning("message_bridge 不可用，无法推送")
        return

    for msg in new_msgs:
        content = msg.get("content", "")
        from_id = msg.get("from", "")

        # 如果是嵌套 JSON（呱呱的 AI 回复格式），提取纯文本
        if content.startswith("{"):
            try:
                inner = json.loads(content)
                payloads = inner.get("result", {}).get("payloads", [])
                if payloads:
                    text = payloads[0].get("text", content)
                else:
                    text = content
            except (json.JSONDecodeError, Exception):
                text = content
        else:
            text = content

        message_bridge.write_incoming(from_id, text, msg.get("msg_id", ""))
        log.info(f"📨 桥接推送: {from_id}→ZS0002 | {text[:60]}")


# ── 主动通知（可选：推送到 AIM 或 QQ） ──

def notify_if_urgent(new_msgs: list):
    """如果发现关键消息，主动通知"""
    for msg in new_msgs:
        content = msg.get("content", "").lower()
        from_id = msg.get("from", "")
        # 检查是否为关键消息（包含 urgent/紧急/重要 等关键词）
        urgent_keywords = ["urgent", "紧急", "重要", "立即", "马上"]
        if any(kw in content for kw in urgent_keywords):
            log.warning(f"⚠️ 紧急消息 from {from_id}: {content[:100]}")
            # TODO: 通过 AIM 或 QQ 主动通知


# ── 主循环 ──

def main():
    log.info("🚀 AIM Message Watcher 启动")
    log.info(f"    监控: {MESSAGES_PATH}")
    log.info(f"    间隔: {SCAN_INTERVAL}s")
    log.info(f"    桥接: {'可用' if bridge_available else '不可用'}")

    state = WatcherState(STATE_FILE)

    # 启动时全量扫描一次
    log.info("启动全量扫描...")
    new_msgs = scan_messages(state)
    if new_msgs:
        log.info(f"发现 {len(new_msgs)} 条未处理消息")
        push_to_bridge(new_msgs)
        notify_if_urgent(new_msgs)
    else:
        log.info("无未处理消息")
    state.save()

    # 定时轮询
    cycle = 0
    while True:
        time.sleep(SCAN_INTERVAL)
        cycle += 1

        new_msgs = scan_messages(state)
        if new_msgs:
            log.info(f"[第{cycle}轮] 发现 {len(new_msgs)} 条新消息")
            push_to_bridge(new_msgs)
            notify_if_urgent(new_msgs)
            state.save()
        else:
            log.debug(f"[第{cycle}轮] 无新消息")


if __name__ == "__main__":
    main()
