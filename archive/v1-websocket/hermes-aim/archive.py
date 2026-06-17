#!/usr/bin/env python3
"""
AIM 聊天记录归档管理器

存储结构：
  ~/.hermes/aim/data/
  ├── messages.jsonl          Hub 全局消息日志（所有Agent的消息）
  └── archive/
      ├── ZS0001/
      │   ├── chat_history.jsonl    呱呱的完整聊天记录
      │   └── sessions.json         会话索引（按对方Agent分组）
      ├── ZS0002/
      │   ├── chat_history.jsonl    吉量的完整聊天记录
      │   └── sessions.json
      └── ZS0003/
          ├── chat_history.jsonl    小火鸡儿的完整聊天记录
          └── sessions.json

命令：
  python3 archive.py list ZS0001              查看某Agent的聊天记录
  python3 archive.py search ZS0001 "关键词"    搜索
  python3 archive.py session ZS0001 ZS0002    查看与某Agent的对话
  python3 archive.py stats ZS0001             统计信息
  python3 archive.py export ZS0001            导出为可读格式
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
ARCHIVE_DIR = BASE_DIR / "data" / "archive"
GLOBAL_LOG = BASE_DIR / "data" / "messages.jsonl"


def ensure_dirs():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for agent_id in ["ZS0001", "ZS0002", "ZS0003"]:
        (ARCHIVE_DIR / agent_id).mkdir(exist_ok=True)


def archive_message(msg: dict):
    """归档一条消息到相关Agent的本地记录"""
    ensure_dirs()
    sender = msg.get("from", "")
    receiver = msg.get("to", "")
    is_group = msg.get("group", False)

    # 发送者的视角
    if sender and sender.startswith("ZS"):
        _append_to_agent(sender, msg, direction="sent")

    # 接收者的视角（单聊）
    if not is_group and receiver and receiver.startswith("ZS"):
        _append_to_agent(receiver, msg, direction="received")

    # 群聊：所有群成员
    if is_group:
        # 从配置读群成员
        config_path = BASE_DIR / "config.json"
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
            group = config.get("groups", {}).get(receiver, {})
            for member_id in group.get("members", []):
                if member_id != sender:
                    _append_to_agent(member_id, msg, direction="received")


def _append_to_agent(agent_id: str, msg: dict, direction: str):
    """追加到指定Agent的聊天记录"""
    agent_dir = ARCHIVE_DIR / agent_id
    agent_dir.mkdir(exist_ok=True)

    # 聊天记录
    history_file = agent_dir / "chat_history.jsonl"
    entry = {
        **msg,
        "direction": direction,
        "archived_at": datetime.now().isoformat(),
    }
    with open(history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # 更新会话索引
    _update_session_index(agent_id, msg, direction)


def _update_session_index(agent_id: str, msg: dict, direction: str):
    """更新会话索引"""
    sessions_file = ARCHIVE_DIR / agent_id / "sessions.json"
    sessions = {}
    if sessions_file.exists():
        try:
            with open(sessions_file, "r", encoding="utf-8") as f:
                sessions = json.load(f)
        except:
            pass

    # 确定对方ID
    if msg.get("group"):
        peer_id = msg.get("to", "unknown")
    elif direction == "sent":
        peer_id = msg.get("to", "unknown")
    else:
        peer_id = msg.get("from", "unknown")

    if peer_id not in sessions:
        sessions[peer_id] = {
            "peer_id": peer_id,
            "first_message": msg.get("time", ""),
            "message_count": 0,
            "last_message": "",
            "last_time": "",
        }

    sessions[peer_id]["message_count"] += 1
    sessions[peer_id]["last_message"] = msg.get("content", "")[:100]
    sessions[peer_id]["last_time"] = msg.get("time", "")

    with open(sessions_file, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def get_recent_context(agent_id: str, peer_id: str = None, limit: int = 10) -> list:
    """获取最近N条消息（用于AI上下文）"""
    history_file = ARCHIVE_DIR / agent_id / "chat_history.jsonl"
    if not history_file.exists():
        return []

    messages = []
    with open(history_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                msg = json.loads(line.strip())
                if peer_id:
                    # 过滤特定对话
                    m_from = msg.get("from", "")
                    m_to = msg.get("to", "")
                    if peer_id not in (m_from, m_to):
                        continue
                messages.append(msg)
            except:
                continue

    return messages[-limit:]


def search_messages(agent_id: str, keyword: str, limit: int = 20) -> list:
    """搜索聊天记录"""
    history_file = ARCHIVE_DIR / agent_id / "chat_history.jsonl"
    if not history_file.exists():
        return []

    results = []
    with open(history_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                msg = json.loads(line.strip())
                if keyword.lower() in msg.get("content", "").lower():
                    results.append(msg)
            except:
                continue

    return results[-limit:]


def get_stats(agent_id: str) -> dict:
    """获取统计信息"""
    history_file = ARCHIVE_DIR / agent_id / "chat_history.jsonl"
    sessions_file = ARCHIVE_DIR / agent_id / "sessions.json"

    stats = {
        "agent_id": agent_id,
        "total_messages": 0,
        "sent": 0,
        "received": 0,
        "group_messages": 0,
        "sessions": {},
    }

    if history_file.exists():
        with open(history_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    msg = json.loads(line.strip())
                    stats["total_messages"] += 1
                    if msg.get("direction") == "sent":
                        stats["sent"] += 1
                    else:
                        stats["received"] += 1
                    if msg.get("group"):
                        stats["group_messages"] += 1
                except:
                    continue

    if sessions_file.exists():
        with open(sessions_file, "r", encoding="utf-8") as f:
            stats["sessions"] = json.load(f)

    return stats


def export_readable(agent_id: str, peer_id: str = None) -> str:
    """导出为可读格式"""
    messages = get_recent_context(agent_id, peer_id, limit=1000)
    if not messages:
        return f"无 {agent_id} 的聊天记录"

    # Agent emoji 映射
    emojis = {"ZS0001": "🐸", "ZS0002": "🐴", "ZS0003": "🐤", "SYSTEM": "⚙️"}

    lines = [f"=== {agent_id} 聊天记录导出 ==="]
    lines.append(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"消息数: {len(messages)}")
    lines.append("")

    for msg in messages:
        sender = msg.get("from", "?")
        emoji = emojis.get(sender, "❓")
        content = msg.get("content", "")
        time_str = msg.get("time", "")
        direction = "→" if msg.get("direction") == "sent" else "←"
        group = "[群]" if msg.get("group") else ""

        lines.append(f"{time_str} {emoji}{sender} {direction} {group}")
        lines.append(f"  {content}")
        lines.append("")

    return "\n".join(lines)


def rebuild_from_global():
    """从全局消息日志重建所有Agent的归档"""
    if not GLOBAL_LOG.exists():
        print(f"全局日志不存在: {GLOBAL_LOG}")
        return

    # 清空现有归档
    import shutil
    if ARCHIVE_DIR.exists():
        shutil.rmtree(ARCHIVE_DIR)
    ensure_dirs()

    count = 0
    with open(GLOBAL_LOG, "r", encoding="utf-8") as f:
        for line in f:
            try:
                msg = json.loads(line.strip())
                archive_message(msg)
                count += 1
            except:
                continue

    print(f"重建完成: {count} 条消息已归档")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "rebuild":
        rebuild_from_global()
    elif cmd == "list":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else "ZS0002"
        stats = get_stats(agent_id)
        print(f"Agent: {stats['agent_id']}")
        print(f"总消息: {stats['total_messages']} (发送:{stats['sent']} 接收:{stats['received']})")
        print(f"群消息: {stats['group_messages']}")
        print(f"\n会话:")
        for peer_id, session in stats.get("sessions", {}).items():
            print(f"  {peer_id}: {session['message_count']}条 | 最后: {session['last_time']}")
    elif cmd == "search":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else "ZS0002"
        keyword = sys.argv[3] if len(sys.argv) > 3 else ""
        if not keyword:
            print("用法: archive.py search <agent_id> <keyword>")
            return
        results = search_messages(agent_id, keyword)
        for msg in results:
            print(f"{msg.get('time','')} [{msg.get('from','?')}] {msg.get('content','')[:80]}")
    elif cmd == "session":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else "ZS0002"
        peer_id = sys.argv[3] if len(sys.argv) > 3 else None
        messages = get_recent_context(agent_id, peer_id)
        for msg in messages:
            d = "→" if msg.get("direction") == "sent" else "←"
            print(f"{msg.get('time','')} {d} [{msg.get('from','?')}] {msg.get('content','')[:80]}")
    elif cmd == "stats":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else "ZS0002"
        stats = get_stats(agent_id)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif cmd == "export":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else "ZS0002"
        peer_id = sys.argv[3] if len(sys.argv) > 3 else None
        print(export_readable(agent_id, peer_id))
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
