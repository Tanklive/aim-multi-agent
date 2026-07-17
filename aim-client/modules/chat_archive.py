#!/usr/bin/env python3
"""
AIM ChatArchive — 聊天记录持久化模块

功能模块模式（非临时补丁）：
- 构造时绑定 agent_id，归档只写自己的目录
- 进程级 msg_id 去重，一进程一实例天然隔离
- JSONL 追加写入，支持 grep/awk 友好
- 内置查询接口：search / get_session / stats / export

用法：
    archive = ChatArchive(agent_id="ZS0001")
    archive.archive(envelope, direction="sent", to_id="ZS0002")
    results = archive.search("关键词")
    stats = archive.get_stats()

目录结构：
    ~/.aim/data/archive/{agent_id}/
    ├── chat_history.jsonl    # 聊天记录（每条一行 JSON）
    └── sessions.json         # 会话索引（按对方 Agent 分组）
"""

import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Optional


class ChatArchive:
    """AIM 聊天归档模块 — 单Agent绑定，进程级去重"""

    def __init__(self, agent_id: str, data_dir: Optional[Path] = None):
        self.agent_id = agent_id
        self.data_dir = data_dir or (Path.home() / ".aim" / "data" / "archive")
        self._seen_ids: set = set()  # 进程级 msg_id 去重
        self._max_seen = 100_000      # 内存上限

    # ── 归档 ──────────────────────────────────────

    def archive(self, envelope: dict, direction: str, to_id: str = "") -> bool:
        """归档一条消息

        Args:
            envelope: AIM 消息信封 (ver/id/ts/from/type/payload)
            direction: 'sent' | 'received'
            to_id: 显式接收方（SDK 信封可能不含 to 字段）

        Returns:
            True 写入成功, False 跳过（空内容/重复）
        """
        if not isinstance(envelope, dict):
            return False  # malformed envelope
        payload = envelope.get("payload", {})
        if not isinstance(payload, dict):
            return False  # malformed payload, can't extract content
        content = payload.get("text", "")

        msg_id = envelope.get("id", "")
        if msg_id:
            if msg_id in self._seen_ids:
                return False  # 进程级去重
            self._seen_ids.add(msg_id)
            if len(self._seen_ids) > self._max_seen:
                self._seen_ids = set(list(self._seen_ids)[-self._max_seen // 2:])

        entry = {
            "id": msg_id,
            "from": envelope.get("from", ""),
            "to": to_id or envelope.get("to", ""),
            "content": content,
            "time": envelope.get("ts", datetime.utcnow().isoformat()),
            "group": envelope.get("type") == "grp",
            "direction": direction,
            "archived_at": datetime.utcnow().isoformat(),
        }

        self._write_jsonl(entry)
        self._update_session_index(entry)
        return True

    # ── 查询 ──────────────────────────────────────

    def search(self, keyword: str, limit: int = 20) -> list:
        """搜索聊天记录（大小写不敏感）"""
        results = []
        keyword_lower = keyword.lower()
        for entry in self._read_all():
            if keyword_lower in entry.get("content", "").lower():
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def get_session(self, peer_id: str, limit: int = 50) -> list:
        """[已废弃] 用 get_messages() 替代，支持 cursor 分页"""
        return self.get_messages(peer_id, limit=limit)[0]

    def get_messages(self, peer_id: str, before: str = None, limit: int = 20,
                     since: str = None, after: str = None) -> tuple:
        """Cursor-based 分页查询（对标微信上拉加载更多）

        Args:
            peer_id: 对方 Agent ID 或群聊 ID
            before: msg_id — 获取此消息之前的 N 条（cursor）
            limit: 每页条数，默认 20
            since: ISO 时间 — 从此时间之后的消息（含），用于时间范围筛选
            after: msg_id — 获取此消息之后的 N 条（反向翻页）

        Returns:
            (messages: list, has_more: bool)
        """
        entries = self._read_all_reverse()  # 倒序，最新的在前

        # 按 peer_id 过滤
        entries = [e for e in entries if peer_id in (e.get("from", ""), e.get("to", ""))]

        # 时间范围过滤
        if since:
            entries = [e for e in entries if e.get("time", "") >= since]

        # Cursor: 定位到 before 之后
        if before:
            found = False
            filtered = []
            for e in entries:
                if not found:
                    eid = e.get("id", "")
                    if eid == before or eid.startswith(before):
                        found = True  # 跳过 cursor 本身，从下一条开始
                    continue
                filtered.append(e)
            entries = filtered
        elif after:
            # 反向翻页：定位到 after，取它之后的
            idx = next((i for i, e in enumerate(entries) if e.get("id", "") == after or e.get("id", "").startswith(after)), -1)
            if idx >= 0:
                entries = entries[:idx][::-1]  # 转回正序
            else:
                entries = []

        # 截取 limit 条
        has_more = len(entries) > limit
        result = entries[:limit]
        return (result, has_more)

    def get_stats(self) -> dict:
        """统计信息"""
        stats = {
            "agent_id": self.agent_id,
            "total_messages": 0,
            "sent": 0,
            "received": 0,
            "group_messages": 0,
            "sessions": self._read_session_index(),
        }
        for entry in self._read_all():
            stats["total_messages"] += 1
            if entry.get("direction") == "sent":
                stats["sent"] += 1
            else:
                stats["received"] += 1
            if entry.get("group"):
                stats["group_messages"] += 1
        return stats

    def export_readable(self, peer_id: str = None, limit: int = 1000) -> str:
        """导出为可读格式"""
        entries = self._read_all()
        if peer_id:
            entries = [e for e in entries if peer_id in (e.get("from", ""), e.get("to", ""))]
        entries = entries[-limit:]

        emojis = {"ZS0001": "🐸", "ZS0002": "🐴", "ZS0003": "🐤", "SYSTEM": "⚙️"}

        lines = [f"=== {self.agent_id} 聊天记录导出 ===",
                 f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                 f"消息数: {len(entries)}", ""]

        for e in entries:
            sender = e.get("from", "?")
            emoji = emojis.get(sender, "❓")
            d = "→" if e.get("direction") == "sent" else "←"
            g = "[群]" if e.get("group") else ""
            lines.append(f"{e.get('time','')} {emoji}{sender} {d} {g}")
            lines.append(f"  {e.get('content','')}")
            lines.append("")
        return "\n".join(lines)

    # ── 底层 I/O ──────────────────────────────────

    @property
    def _agent_dir(self) -> Path:
        d = self.data_dir / self.agent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_jsonl(self, entry: dict):
        """追加一行 JSON 到 chat_history.jsonl"""
        history = self._agent_dir / "chat_history.jsonl"
        with open(history, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _read_all(self) -> list:
        """读取全部聊天记录（正序）"""
        history = self._agent_dir / "chat_history.jsonl"
        if not history.exists():
            return []
        entries = []
        with open(history, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except Exception:
                    continue
        return entries

    def _read_all_reverse(self) -> list:
        """读取全部聊天记录（倒序：最新的在前）"""
        history = self._agent_dir / "chat_history.jsonl"
        if not history.exists():
            return []
        entries = []
        with open(history, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except Exception:
                    continue
        entries.reverse()
        return entries

    def _update_session_index(self, entry: dict):
        """更新会话索引"""
        sessions_file = self._agent_dir / "sessions.json"
        sessions = self._read_session_index()

        # 确定对方 ID
        if entry.get("group"):
            peer_id = entry.get("to", "unknown")
        elif entry.get("direction") == "sent":
            peer_id = entry.get("to", "unknown")
        else:
            peer_id = entry.get("from", "unknown")

        if peer_id not in sessions:
            sessions[peer_id] = {
                "peer_id": peer_id,
                "first_message": entry.get("time", ""),
                "message_count": 0,
                "last_message": "",
                "last_time": "",
            }

        sessions[peer_id]["message_count"] += 1
        sessions[peer_id]["last_message"] = entry.get("content", "")[:100]
        sessions[peer_id]["last_time"] = entry.get("time", "")

        with open(sessions_file, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)

    def _read_session_index(self) -> dict:
        sessions_file = self._agent_dir / "sessions.json"
        if not sessions_file.exists():
            return {}
        try:
            with open(sessions_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
