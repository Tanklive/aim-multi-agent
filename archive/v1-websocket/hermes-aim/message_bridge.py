#!/usr/bin/env python3
"""
AIM 消息桥接 — 从 AIM 到主会话

作用：aim-agent 收到呱呱消息后，写入此文件。
主会话（Hermes CLI）通过 heartbeat 扫描此文件获取新消息。

位置：~/shared/aim-client/pending_incoming.jsonl
格式：JSONL，每条包含 from, content, timestamp, msg_id
"""

import json
import os
import time
from datetime import datetime

BRIDGE_FILE = os.path.expanduser("~/shared/aim-client/pending_incoming.jsonl")
MAX_ENTRIES = 100  # 最多保留100条

def write_incoming(from_id: str, content: str, msg_id: str = ""):
    """aim-agent 收到消息时调用此函数写入"""
    entry = {
        "from": from_id,
        "content": content,
        "ts": time.time(),
        "datetime": datetime.now().strftime("%H:%M:%S"),
        "msg_id": msg_id,
    }
    
    os.makedirs(os.path.dirname(BRIDGE_FILE), exist_ok=True)
    
    with open(BRIDGE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    # 控制文件大小
    _trim_file()
    
    return True

def read_new(since_ts: float = 0) -> list:
    """读取 since_ts 之后的新消息（主会话 heartbeat 调用）"""
    if not os.path.exists(BRIDGE_FILE):
        return []
    
    new_msgs = []
    with open(BRIDGE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("ts", 0) > since_ts:
                    new_msgs.append(entry)
            except json.JSONDecodeError:
                continue
    
    return new_msgs

def get_summary() -> str:
    """获取消息摘要（主会话注入上下文用）"""
    msgs = read_new(time.time() - 300)  # 最近5分钟
    if not msgs:
        return ""
    
    lines = ["[AIM 消息桥接 - 最近5分钟]"]
    for msg in msgs:
        f = msg.get("from", "?")
        c = msg.get("content", "")[:80]
        t = msg.get("datetime", "")
        lines.append(f"  [{t}] {f}: {c}")
    
    return "\n".join(lines)

def _trim_file():
    """控制文件大小"""
    if not os.path.exists(BRIDGE_FILE):
        return
    
    with open(BRIDGE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    if len(lines) <= MAX_ENTRIES:
        return
    
    # 只保留最新的 MAX_ENTRIES 条
    with open(BRIDGE_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines[-MAX_ENTRIES:])

# 如果直接运行，显示当前桥接状态
if __name__ == "__main__":
    print(f"桥接文件: {BRIDGE_FILE}")
    print(f"文件存在: {os.path.exists(BRIDGE_FILE)}")
    
    if os.path.exists(BRIDGE_FILE):
        with open(BRIDGE_FILE, "r") as f:
            lines = [l for l in f if l.strip()]
        print(f"当前条目: {len(lines)}")
        
        # 显示最近3条
        print("\n最近消息:")
        for line in lines[-3:]:
            try:
                entry = json.loads(line)
                print(f"  [{entry.get('datetime','')}] {entry.get('from','')}: {entry.get('content','')[:60]}")
            except:
                pass
