#!/usr/bin/env python3
"""
AIM QQ 消息转发标准脚本

每个 Agent 的 QQ Bot（或其他外部入口）收到消息后，调用此脚本即可自动转发到 AIM。

用法（在 QQ Bot handler 中加一行）：
  python3 aim_qq_forward.py "<消息内容>" "<发送者ID>"

示例：
  python3 aim_qq_forward.py "@吉量 你好" "USER123"

自动识别 @目标并转发到对应 Agent。
无 @目标时，转发到 grp_trio 群聊（让所有 Agent 看到）。
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

AIM_DIR = Path.home() / ".hermes" / "aim"
AIM_SEND = AIM_DIR / "aim_send.py"

# Agent ID 与名字的映射（用于解析 @）
AGENT_MAP = {
    "ZS0001": ["呱呱", "guagua"],
    "ZS0002": ["吉量", "jiliang"],
    "ZS0003": ["小火鸡儿", "xiaohuoji", "小火鸡"],
}

def get_my_agent_id() -> str:
    """自动检测本 Agent 的身份"""
    config_file = AIM_DIR / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            return config.get("node_id", "ZS0002")
        except:
            pass
    return "ZS0002"


def parse_targets(text: str) -> list:
    """解析消息中的 @ 目标"""
    targets = []
    
    # @AgentID 或 @名字
    for aid, names in AGENT_MAP.items():
        # 匹配 @ZS0001、@ZS0002、@ZS0003
        if f"@{aid}" in text:
            targets.append(aid)
            continue
        # 匹配 @呱呱、@吉量、@小火鸡儿
        for name in names:
            if f"@{name}" in text:
                targets.append(aid)
                break
    
    return targets


def forward(message: str, sender_id: str = "", from_agent: str = "") -> dict:
    """转发消息到 AIM"""
    if not from_agent:
        from_agent = get_my_agent_id()
    
    # 解析 @ 目标
    targets = parse_targets(message)
    
    if not targets:
        # 无 @ 目标 → 发到群聊
        return send_to_aim("grp_trio", message, from_agent, group=True)
    
    # 有 @ 目标 → 逐个转发
    results = []
    for target in targets:
        # 去掉消息中的 @ 前缀，让目标 Agent 收到干净内容
        clean_msg = message
        for aid, names in AGENT_MAP.items():
            clean_msg = clean_msg.replace(f"@{aid}", "").strip()
            for name in names:
                clean_msg = clean_msg.replace(f"@{name}", "").strip()
        
        result = send_to_aim(target, clean_msg or message, from_agent)
        results.append(result)
    
    return results


def send_to_aim(target: str, content: str, from_agent: str, group: bool = False) -> dict:
    """通过 aim_send.py 发送"""
    try:
        env = os.environ.copy()
        env["no_proxy"] = "127.0.0.1,localhost"
        
        if group:
            cmd = [sys.executable, str(AIM_SEND), target, content, "--group", "--from", from_agent]
        else:
            cmd = [sys.executable, str(AIM_SEND), target, content, "--from", from_agent]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
        return {"success": result.returncode == 0, "target": target, "output": result.stdout.strip()}
    except Exception as e:
        return {"success": False, "target": target, "error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("AIM QQ 消息转发")
        print()
        print("用法（在 QQ Bot handler 中加一行）：")
        print(f"  python3 {sys.argv[0]} \"<消息内容>\"")
        print(f"  python3 {sys.argv[0]} \"<消息内容>\" \"<发送者ID>\"")
        print(f"  python3 {sys.argv[0]} \"<消息内容>\" \"<发送者ID>\" --from ZS0001")
        print()
        print("示例：")
        print(f'  python3 {sys.argv[0]} "@吉量 你好"')
        print(f'  python3 {sys.argv[0]} "@呱呱 帮我查个东西" "USER_QQ" --from ZS0003')
        sys.exit(1)
    
    message = sys.argv[1]
    sender_id = sys.argv[2] if len(sys.argv) > 2 else ""
    from_agent = ""
    
    if "--from" in sys.argv:
        idx = sys.argv.index("--from")
        if idx + 1 < len(sys.argv):
            from_agent = sys.argv[idx + 1]
    
    results = forward(message, sender_id, from_agent)
    
    if isinstance(results, list):
        for r in results:
            s = "✅" if r.get("success") else "❌"
            print(f"{s} {r.get('target','?')}: {r.get('output', r.get('error',''))}")
    else:
        s = "✅" if results.get("success") else "❌"
        target = results.get("target", "群聊")
        print(f"{s} → {target}: {results.get('output', results.get('error',''))}")
