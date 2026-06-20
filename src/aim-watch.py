#!/usr/bin/env python3
"""
AIM Watch v2.0 — 标准版终端监控

适配 TOP10 Agent 框架的通用 Observer 事件监控终端。
基于 SDK AIMObserverClient（只读连接），展示所有 Agent 的
消息收发 + AI 处理过程 + 系统事件。

协议：
  - Observer 事件: aim.obs.>（emit_obs 推送）
  - 消息: aim.dm.> / aim.grp.>（可选，默认显示）
  - 历史: JetStream aim-observations（--history N）

用法:
  aim-watch                           # 所有 Agent
  aim-watch --agent ZS0001            # 只看呱呱
  aim-watch --history 10              # 启动时回放 10 条
  aim-watch --compact                 # 紧凑模式
  aim-watch --json                    # JSON 行输出
  aim-watch --show-heartbeat          # 显示心跳（默认隐藏）
  aim-watch --save /tmp/watch.log     # 同时写入文件

设计原则：
  - 只读：不发布任何消息
  - 框架无关：只处理标准 Observer 事件
  - 低资源：单线程异步，~2MB 内存
  - 零依赖：只需 nats-py + SDK

Author: 吉量 🐴 (ZS0002)
Protocol: AIM Veritas (§4 Observer)
"""

import asyncio
import json
import os
import sys
import time
import argparse
import signal
from datetime import datetime
from pathlib import Path

# SDK 路径
SDK_DIR = Path.home() / ".aim" / "bin"
if str(SDK_DIR) not in sys.path:
    sys.path.insert(0, str(SDK_DIR))

from aim_nats_sdk import AIMObserverClient


# ── 持久化旋转 ────────────────────────────────────

def _rotate_if_needed(filepath: str, max_lines: int = 50000):
    """Keep the last `max_lines` lines of a file to prevent unbounded growth."""
    path = Path(filepath).expanduser()
    if not path.exists():
        return
    try:
        # Quick estimate: if file is small, skip rotation
        if path.stat().st_size < 10 * 1024 * 1024:  # < 10MB, rough
            with open(path) as f:
                line_count = sum(1 for _ in f)
            if line_count <= max_lines:
                return
        # Trim to last max_lines
        with open(path) as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(path, "w") as f:
                f.writelines(lines[-max_lines:])
    except Exception:
        pass  # rotation is best-effort


# ══════════════════════════════════════════════════════════════
#  配置
# ══════════════════════════════════════════════════════════════

VERSION = "2.2.0"

# Agent ID → 框架映射（从 aim.json 自动加载，也支持 fallback）
AGENT_FRAMEWORK_CACHE: dict = {}  # agent_id → framework

# 状态图标
STATUS_ICONS = {
    # 消息生命周期
    "received": "📥",
    "processing": "⚙️",
    "completed": "✅",
    "error": "❌",
    # AI 处理
    "ai_start": "🤖",
    "ai_thinking": "🤔",
    "ai_tool_call": "🔧",
    "ai_tool_result": "📎",
    "ai_done": "✅",
    "ai_empty": "⚠️",
    # 系统事件
    "agent_online": "🟢",
    "agent_offline": "🔴",
    "heartbeat": "💓",
    # 消息类型（备用）
    "dm": "📨",
    "grp": "📢",
}

# Agent 头像映射
AGENT_EMOJI = {
    "ZS0001": "🐸",
    "ZS0002": "✨🐴✨",
    "ZS0003": "🐤",
}

# Observer 事件默认隐藏（纯噪音）
SILENT_STATUSES = {"heartbeat", "delivered"}

# AI 过程事件（compact 模式合并）
AI_PROCESS_EVENTS = {"ai_start", "ai_thinking", "ai_tool_call", "ai_tool_result", "ai_done", "ai_empty"}


def load_agent_frameworks(config: dict) -> dict:
    """从 aim.json 加载 Agent→Framework 映射"""
    mapping = {}
    agents = config.get("agents", {})
    if isinstance(agents, dict):
        for agent_id, info in agents.items():
            if isinstance(info, dict) and "framework" in info:
                mapping[agent_id] = info["framework"]
    return mapping


def load_agent_names(config: dict) -> dict:
    """从 aim.json 加载 Agent ID→名称映射"""
    mapping = {}
    agents = config.get("agents", {})
    if isinstance(agents, dict):
        for agent_id, info in agents.items():
            if isinstance(info, dict) and "name" in info:
                mapping[agent_id] = info["name"]
    return mapping


def fmt_time(ts) -> str:
    """格式化时间戳为 HH:MM:SS"""
    try:
        if isinstance(ts, str) and "T" in ts:
            from datetime import datetime as dt2, timezone
            dt_obj = dt2.fromisoformat(ts.replace("Z", "+00:00"))
            return dt_obj.astimezone().strftime("%H:%M:%S")
        ts_f = float(ts) if ts else 0
        if ts_f <= 0:
            return "??:??:??"
        return datetime.fromtimestamp(ts_f).strftime("%H:%M:%S")
    except (ValueError, TypeError, OSError):
        return "??:??:??"


# ══════════════════════════════════════════════════════════════
#  显示核心
# ══════════════════════════════════════════════════════════════


class WatchDisplay:
    """AIM Watch 显示引擎

    职责：
    - 格式化事件为终端可读文本
    - 支持 compact / json 模式
    - 支持文件输出（--save）
    - 追踪 Agent 在线状态
    """

    def __init__(self, json_mode=False, compact=False, show_heartbeat=False,
                 save_path="", agent_filter=">", framework_filter="",
                 framework_map: dict = None, agent_name_map: dict = None,
                 max_text=0, channel_filter=None):  # channel_filter: None=all, "dm", "grp"
        self.json_mode = json_mode
        self.compact = compact
        self.show_heartbeat = show_heartbeat
        self.save_path = save_path
        self.agent_filter = agent_filter
        self.framework_filter = framework_filter
        self.framework_map = framework_map or {}  # agent_id → framework
        self.agent_name_map = agent_name_map or {}  # agent_id → 显示名称
        self.save_fp = None
        self.max_text = max_text  # 消息预览最大字符数（0=不截断）
        self.channel_filter = channel_filter  # "dm" / "grp" / None

        # 统计
        self.total_events = 0
        self.displayed_events = 0
        self.agent_online: dict = {}  # agent_id → ts
        self.hearbeat_seen: set = set()  # agent_id set
        self.messages_seen = 0
        self.event_types: dict = {}  # status → count

        # compact 模式追踪每个 msg_id 的 ai 事件链
        self._ai_chains: dict = {}  # msg_id → [events...]
        # 去重：SDK emit_obs 双发(JS+raw)导致重复
        # 使用 nonce（唯一值，JS+raw 相同）替代 ts_int 做精确去重
        self._seen_events: set = set()  # (agent_id, nonce)
        # 频道追踪：msg_id → "dm"|"grp"，配合 channel_filter 过滤 observer 事件
        self._msg_channel: dict = {}

        # 打开保存文件
        if save_path:
            try:
                self.save_fp = open(save_path, "a", encoding="utf-8")
            except OSError as e:
                print(f"⚠️ 无法写入保存文件 {save_path}: {e}", file=sys.stderr)

        # 横幅
        self._banner_shown = False

    def show_banner(self, server: str, target: str):
        """显示启动横幅"""
        if self.json_mode:
            return
        agent_label = target if target != ">" else "All Agents"
        fw_label = f"  🔧 {self.framework_filter}" if self.framework_filter else ""
        ch_label = ""
        if self.channel_filter == "dm":
            ch_label = "  📨 DM only"
        elif self.channel_filter == "grp":
            ch_label = "  📢 群聊 only"
        print(f"┌─ AIM Watch v{VERSION} ────────────────────────────────── {datetime.now().strftime('%H:%M:%S')} ─┐")
        print(f"│ 📡 {server}")
        print(f"│ 🎯 {agent_label}{fw_label}{ch_label}")
        print("├────────────────────────────────────────────────────────────────────────┤")
        self._banner_shown = True

    def display(self, event: dict):
        """处理并显示一条事件"""
        self.total_events += 1

        status = event.get("status", "")
        agent_id = event.get("agent_id", "???")
        framework = event.get("framework", "")

        # 去重：SDK emit_obs 双发(JS+raw)导致重复 — 用 nonce 精确去重
        nonce = event.get("nonce", "")
        dedup_key = (agent_id, nonce) if nonce else (agent_id, msg_id, status, int(event.get("ts", 0) * 10))
        if dedup_key in self._seen_events:
            return
        self._seen_events.add(dedup_key)
        # 定期清理旧 key（保留最近 10000 条）
        if len(self._seen_events) > 10000:
            self._seen_events = set(list(self._seen_events)[-5000:])

        # 框架解析：事件里没有 framework 字段时，从映射表查找
        if not framework and agent_id in self.framework_map:
            framework = self.framework_map[agent_id]

        # 过滤
        if self.agent_filter != ">" and agent_id != self.agent_filter:
            return
        if self.framework_filter and framework != self.framework_filter:
            return
        if not self.show_heartbeat and status in SILENT_STATUSES:
            return

        # 频道过滤：observer received 事件在 dm-only/grp-only 模式不显示
        # 正文由 _show_message 正确按频道过滤显示
        if self.channel_filter and status == "received":
            return

        self.displayed_events += 1

        # 统计
        self.event_types[status] = self.event_types.get(status, 0) + 1

        # Agent 在线追踪
        if status == "agent_online":
            self.agent_online[agent_id] = time.time()
            self.hearbeat_seen.add(agent_id)
        elif status == "agent_offline":
            self.agent_online.pop(agent_id, None)
        elif status == "heartbeat":
            self.hearbeat_seen.add(agent_id)

        if self.json_mode:
            self._output_json(event)
        elif self.compact and status in AI_PROCESS_EVENTS:
            self._display_compact_ai(event)
        else:
            self._display_line(event)

    def _output_json(self, event: dict):
        """JSON 行输出"""
        line = json.dumps(event, ensure_ascii=False)
        print(line, flush=True)
        if self.save_fp:
            self.save_fp.write(line + "\n")
            self.save_fp.flush()

    def _display_line(self, event: dict):
        """单行显示一条事件"""
        status = event.get("status", "")
        agent_id = event.get("agent_id", "???")
        msg_id = event.get("msg_id", "")
        detail = event.get("detail", "")
        ts = event.get("ts", 0)
        meta = event.get("meta", {})
        framework = event.get("framework", "")

        time_str = fmt_time(ts)
        icon = STATUS_ICONS.get(status, "📢")

        # ── received 事件：聊天风格 ──
        if status == "received" and detail:
            import re
            from_id = ""
            text = ""
            m = re.match(r'from=(\S+)\s+text=(.*)', detail, re.DOTALL)
            if m:
                from_id, text = m.group(1), m.group(2)
            if not from_id:
                from_id = meta.get("from_id", "")
                text = detail
            display_name = self.agent_name_map.get(from_id, from_id)
            emoji = AGENT_EMOJI.get(from_id, "")
            line = f"{time_str} 📥 {emoji} {display_name}: {text[:120]}"
            print(line, flush=True)
            if self.save_fp:
                self.save_fp.write(line + "\n")
                self.save_fp.flush()
            return

        # 框架标签（有过滤时显示，或 agent_online 时显示）
        fw_label = ""
        if framework and (self.framework_filter or status == "agent_online"):
            fw_label = f"[{framework}]"

        parts = [f"{time_str} {icon} {self.agent_name_map.get(agent_id, agent_id)}"]
        if fw_label:
            parts.append(fw_label)

        if status:
            parts.append(status)
        if detail:
            # 显示的 detail 截断到 120 字符
            parts.append(f"— {detail[:120]}")

        line = " ".join(parts)
        print(line, flush=True)
        if self.save_fp:
            self.save_fp.write(line + "\n")
            self.save_fp.flush()

    def _display_compact_ai(self, event: dict):
        """紧凑模式：将 ai_start→...→ai_done/ai_empty 合并为 1 行"""
        msg_id = event.get("msg_id", "")
        status = event.get("status", "")
        agent_id = event.get("agent_id", "???")
        ts = event.get("ts", 0)
        detail = event.get("detail", "")[:60]

        if status == "ai_start":
            self._ai_chains[msg_id] = [event]
        elif status in ("ai_done", "ai_empty") and msg_id in self._ai_chains:
            chain = self._ai_chains.pop(msg_id, [])
            chain.append(event)
            # 合并显示
            start_ts = chain[0].get("ts", ts)
            icon = STATUS_ICONS.get(chain[-1]["status"], "✅")
            duration = ts - start_ts if ts > start_ts else 0
            time_str = fmt_time(start_ts)
            dur_str = f" ⏱{duration:.1f}s" if duration >= 1 else ""
            part_count = len(chain)

            print(f"{time_str} {icon} {agent_id} AI ➜ {detail[:60]}{dur_str} ({part_count} steps)", flush=True)
        else:
            # 不在链中的 ai 事件直接显示
            self._display_line(event)

    def show_footer(self):
        """显示结束统计"""
        if self.json_mode:
            return

        online_count = len(self.agent_online)
        alive_count = len(self.hearbeat_seen)

        print("├────────────────────────────────────────────────────────────────────────┤")
        print(f"│ 📊 {self.total_events} events ({self.displayed_events} shown)")
        print(f"│ 🟢 {online_count} online  |  💓 {alive_count} alive")
        if self.event_types:
            types_summary = " | ".join(
                f"{STATUS_ICONS.get(k, '?')} {k}={v}"
                for k, v in sorted(self.event_types.items())
                if k not in SILENT_STATUSES or self.show_heartbeat
            )
            print(f"│ {types_summary}")
        print("└────────────────────────────────────────────────────────────────────────┘")
        self._banner_shown = False

    def close(self):
        """清理资源"""
        if self.save_fp:
            self.save_fp.close()
            self.save_fp = None


# ══════════════════════════════════════════════════════════════
#  事件源
# ══════════════════════════════════════════════════════════════


class EventSource:
    """事件源管理器

    整合三种事件源：
    1. Observer 事件（aim.obs.>）— AI 处理过程
    2. 消息事件（aim.dm.> / aim.grp.>）— 消息收发
    3. JetStream 历史（aim-observations）— 回放
    4. JSONL 文件（--file）— 离线回放（experimental）
    """

    def __init__(self, observer: AIMObserverClient, display: WatchDisplay,
                 since: float = 0):
        self.observer = observer
        self.display = display
        self.since = since  # --since 过滤：Unix 时间戳下限
        self._nc = None

    async def connect(self, server: str, token: str):
        """连接 NATS（Observer  + 消息订阅双通道）"""
        # 1. Observer 通道 — 用 AIMObserverClient
        await self.observer.connect()

        # 2. 消息订阅通道 — 直接创建独立 NATS 连接
        import nats as _nats, os as _os
        msg_kwargs = {
            "servers": [server],
            "max_reconnect_attempts": -1,
            "reconnect_time_wait": 2,
            "ping_interval": 30,
            "max_outstanding_pings": 5,
            "name": f"AIM-Watch-Msg-{self.observer.observer_id}",
        }
        # 自动识别：.creds/.nkey 文件→user_credentials，否则→token
        if _os.path.isfile(token):
            msg_kwargs["user_credentials"] = token
        else:
            msg_kwargs["token"] = token
        self._nc = await _nats.connect(**msg_kwargs)
        return self

    async def run(self, server: str, target: str = ">"):
        """启动事件订阅"""
        # 1. Observer 事件（核心）— 用 async 包装同步 display
        async def _on_obs(event: dict):
            self.display.display(event)
        await self.observer.subscribe(_on_obs, agent_filter=target)

        # 2. 消息事件（可选，默认显示）
        # 用独立 handler 转发到 display
        async def on_msg(envelope: dict):
            """处理消息事件并显示"""
            msg_type = envelope.get("type", "")
            if msg_type in ("dm", "grp"):
                self.display.messages_seen += 1
                self._show_message(envelope)

        await self._subscribe_messages(target, on_msg)

        self.display.show_banner(server, target)
        return self

    async def _subscribe_messages(self, target: str, handler):
        """订阅消息事件（支持 --dm-only / --grp-only 过滤）"""
        if not self._nc or not self._nc.is_connected:
            return

        ch = self.display.channel_filter  # None=all, "dm", "grp"

        if ch is None or ch == "dm":
            async def _on_dm(msg):
                try:
                    env = json.loads(msg.data.decode())
                    await handler(env)
                except Exception:
                    pass
            await self._nc.subscribe("aim.dm.>", cb=_on_dm)

        if ch is None or ch == "grp":
            async def _on_grp(msg):
                try:
                    env = json.loads(msg.data.decode())
                    await handler(env)
                except Exception:
                    pass
            await self._nc.subscribe("aim.grp.>", cb=_on_grp)

    def _show_message(self, envelope: dict):
        """格式化并显示一条消息事件

        格式:
          群聊: HH:MM:SS 昵称 头像: @提及 消息内容 头像
          私聊: HH:MM:SS 昵称 头像 → 对方: 消息内容 头像
        """
        msg_type = envelope.get("type", "?")
        from_id = envelope.get("from", "?")
        msg_id = envelope.get("id", "")
        # 频道追踪：让 observer 事件也能按 channel_filter 过滤
        if msg_id and msg_type in ("dm", "grp"):
            self.display._msg_channel[msg_id] = msg_type
        to_id = envelope.get("to", "")
        payload = envelope.get("payload", {})
        text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        ts = envelope.get("ts", 0)
        meta = envelope.get("meta", {})

        # 显示名称映射
        display_from = self.display.agent_name_map.get(from_id, from_id)
        display_to = self.display.agent_name_map.get(to_id, to_id) if to_id else ""

        # 头像
        emoji = AGENT_EMOJI.get(from_id, "")
        emoji_to = AGENT_EMOJI.get(to_id, "") if to_id else ""

        time_str = fmt_time(ts)
        text_preview = text if self.display.max_text == 0 else text[:self.display.max_text]
        text_preview = text_preview.replace("\\n", "\n")

        # 提取 @提及（从消息中找 @呱呱 @吉量 @小火鸡儿 模式）
        import re
        at_pattern = r'@(?:呱呱|吉量|小火鸡儿|ZS000[1-3])'
        at_mentions = re.findall(at_pattern, text_preview)

        # 构建前缀
        if msg_type == "grp":
            # 群聊: 时间 昵称 头像: (@提及) 消息
            if at_mentions:
                at_str = " ".join(at_mentions) + " "
            else:
                at_str = ""
            prefix = f"{time_str} {display_from} {emoji}: {at_str}"
        else:
            # 私聊: 时间 昵称 头像 → 对方: 消息
            arrow = f" → {display_to} {emoji_to}" if display_to else ""
            prefix = f"{time_str} {display_from} {emoji}{arrow}: "

        # 后置头像
        suffix = f" {emoji}"

        # 计算缩进（用于续行，不含后置头像）
        indent = " " * (len(time_str) + len(display_from) + 3)

        paragraphs = text_preview.split("\n")
        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue
            if i == 0:
                line = f"{prefix}{para}"
            else:
                line = f"{indent}{para}"
            print(line, flush=True)
            if self.display.save_fp:
                self.display.save_fp.write(line + "\n")
                self.display.save_fp.flush()

    async def replay_history(self, count: int, target: str = ">"):
        """回放历史 Observer 事件（支持 --since 时间过滤）"""
        start_time = self.since if self.since else 0
        events = await self.observer.get_history(
            agent_filter=target,
            start_time=start_time,
            end_time=0,
            page=1,
            page_size=count,
        )
        replayed = 0
        for event in events:
            self.display.display(event)
            replayed += 1
        if replayed > 0:
            print(f"\n📜 已回放 {replayed} 条历史\n", flush=True)
        return replayed

    async def replay_file(self, file_path: str):
        """回放 JSONL 文件（experimental）"""
        from pathlib import Path as _Path
        p = _Path(file_path).expanduser()
        if not p.exists():
            print(f"❌ 文件不存在: {file_path}", file=sys.stderr)
            return 0
        replayed = 0
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # --since 过滤
                if self.since:
                    event_ts = event.get("ts", 0)
                    if isinstance(event_ts, str):
                        # ISO string → float
                        try:
                            from datetime import datetime as _dt
                            event_ts = _dt.fromisoformat(
                                event_ts.replace("Z", "+00:00")
                            ).timestamp()
                        except (ValueError, TypeError):
                            event_ts = 0
                    if float(event_ts) < self.since:
                        continue
                self.display.display(event)
                replayed += 1
        if replayed > 0:
            print(f"\n📜 已回放 {replayed} 条文件记录\n", flush=True)
        return replayed

    async def wait_forever(self):
        """永久运行"""
        await self.observer.wait_forever()


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════


def load_config() -> dict:
    """加载 AIM 配置"""
    config_path = Path.home() / ".aim" / "config" / "aim.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def main():
    parser = argparse.ArgumentParser(
        description=f"AIM Watch v{VERSION} — 多 Agent 处理流程只读监控",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  aim-watch                           # 所有 Agent + 全部消息
  aim-watch --agent ZS0001            # 只看呱呱
  aim-watch --dm-only                 # 只看私聊
  aim-watch --grp-only                # 只看群聊
  aim-watch --all --grp-only          # 全 Agent 群聊
  aim-watch --all --dm-only           # 全 Agent 私聊
  aim-watch --history 10              # 启动时回放最近 10 条
  aim-watch --compact                 # 紧凑模式（合并 AI 过程）
  aim-watch --json                    # JSON 行输出
  aim-watch --show-heartbeat          # 显示心跳
  aim-watch --save /tmp/watch.log     # 同时写入文件
  aim-watch --since 3600              # 只看过去 1 小时
  aim-watch --framework hermes        # 只看 Hermes 框架的 Agent
  aim-watch --file /tmp/watch.log     # 离线回放 JSONL 文件 [experimental]

安全:
  - 只读模式，不发布任何消息
  - 需要 NATS JWT/NKey 凭据（从 ~/.aim/config/aim.json 读取 creds_path）
  - 基于 SDK AIMObserverClient（只读连接）

协议:
  - Observer 事件: aim.obs.>（emit_obs 推送）
  - 消息: aim.dm.> / aim.grp.>
  - 历史: JetStream aim-observations
""",
    )
    parser.add_argument("--agent", default=">",
                        help="监控指定 Agent（默认 > 看全部）")
    parser.add_argument("--all", action="store_true",
                        help="显示所有 Agent（默认只看当前 Agent）")
    parser.add_argument("--framework", default="",
                        help="按框架过滤（hermes/openclaw/letta 等）")
    parser.add_argument("--history", type=int, default=0,
                        help="启动时回放最近 N 条 Observer 事件")
    parser.add_argument("--compact", action="store_true",
                        help="紧凑模式（合并 AI 过程，隐藏心跳）")
    parser.add_argument("--json", action="store_true",
                        help="JSON 行输出（用于管道/grep）")
    parser.add_argument("--show-heartbeat", action="store_true",
                        help="显示心跳事件（默认隐藏）")
    parser.add_argument("--save", default="",
                        help="事件同时写入文件路径")
    parser.add_argument("--persist", action="store_true",
                        help="持久化模式: 自动保存到 ~/.aim/system/observer.jsonl (旋转 50000 行)")
    parser.add_argument("--file", default="",
                        help="[experimental] 从 JSONL 文件回放（离线模式，不连 NATS）")
    parser.add_argument("--since", type=int, default=0,
                        help="只看过去 N 秒的事件（配合 --history 或 --file 过滤）")
    parser.add_argument("--full-text", action="store_true",
                        help="显示完整消息内容（不截断）")
    parser.add_argument("--dm-only", action="store_true",
                        help="只显示私聊消息（aim.dm.>）")
    parser.add_argument("--grp-only", action="store_true",
                        help="只显示群聊消息（aim.grp.>）")
    parser.add_argument("--max-text", type=int, default=0,
                        help="消息预览最大字符数（0=不截断）")
    parser.add_argument("--nats-url", default="",
                        help="NATS Server URL（默认从配置读取）")
    parser.add_argument("--version", action="store_true",
                        help="显示版本号")

    args = parser.parse_args()

    if args.version:
        print(f"AIM Watch v{VERSION}")
        sys.exit(0)

    # --full-text 等于 --max-text 0
    if args.full_text:
        args.max_text = 0

    # compact 模式隐含隐藏心跳
    if args.compact:
        args.show_heartbeat = False

    # 通道过滤：--dm-only / --grp-only 互斥
    channel_filter = None
    if args.dm_only and args.grp_only:
        print("❌ --dm-only 和 --grp-only 不能同时使用", file=sys.stderr)
        sys.exit(1)
    elif args.dm_only:
        channel_filter = "dm"
    elif args.grp_only:
        channel_filter = "grp"

    config = load_config()
    server = args.nats_url or config.get("nats_server", "nats://127.0.0.1:4222")
    token = config.get("nats_token", "")
    # JWT 认证优先：从 config 读 creds_path
    if not token:
        creds_path = config.get("nats_jwt_path", "")
        if not creds_path:
            creds_path = config.get("agents", {}).get("admin-watch", {}).get("creds_path", "")
        if creds_path:
            token = Path(creds_path).expanduser().as_posix()

    # 默认只看自己，加 --all 才看全部
    show_all = args.all or (args.agent != ">")
    agent_id_from_config = config.get("agent_id", "ZS0001")

    # Agent→Framework 映射
    fw_map = load_agent_frameworks(config)

    # Agent ID→名称映射（aim-watch 显示用）
    name_map = load_agent_names(config)

    # 离线文件回放模式（不连 NATS）
    if args.file:
        display = WatchDisplay(
            json_mode=args.json,
            compact=args.compact,
            show_heartbeat=args.show_heartbeat,
            save_path=args.save,
            agent_filter=args.agent,
            framework_filter=args.framework,
            framework_map=fw_map,
            agent_name_map=name_map,
            max_text=args.max_text,
            channel_filter=channel_filter,
        )
        since_ts = time.time() - args.since if args.since else 0
        source = EventSource(None, display, since=since_ts)
        display.show_banner("file://" + args.file, args.agent)
        asyncio.run(source.replay_file(args.file))
        display.show_footer()
        display.close()
        return

    # ── Persist: 持久化模式 ──
    if args.persist:
        persist_path = os.path.expanduser("~/.aim/system/observer.jsonl")
        _rotate_if_needed(persist_path, max_lines=50000)
        args.save = persist_path

    if not token:
        print("❌ 未找到 NATS 认证凭据，请检查 ~/.aim/config/aim.json")
        sys.exit(1)

    # 创建组件
    display = WatchDisplay(
        json_mode=args.json,
        compact=args.compact,
        show_heartbeat=args.show_heartbeat,
        save_path=args.save,
        agent_filter=agent_id_from_config if not show_all else ">",
        framework_filter=args.framework,
        framework_map=fw_map,
        agent_name_map=name_map,
        max_text=args.max_text,
        channel_filter=channel_filter,
    )

    observer = AIMObserverClient(
        observer_id=f"aim-watch-{agent_id_from_config}",
        server=server,
        credentials=token,
        num_workers=1,
    )

    since_ts = time.time() - args.since if args.since else 0
    source = EventSource(observer, display, since=since_ts)

    async def run():
        # 连接
        await source.connect(server, token)

        # 历史回放
        if args.history > 0:
            await source.replay_history(args.history, args.agent)

        # 启动实时监控
        await source.run(server, args.agent)

        # 等待 ctrl+c
        await source.wait_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        display.show_footer()
        display.close()
    except Exception as e:
        print(f"\n❌ 异常退出: {e}", file=sys.stderr)
        display.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
