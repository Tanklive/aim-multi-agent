#!/usr/bin/env python3
"""
AIM agent 自启动注册工具

注册 aim-agent.py 为 launchd 服务（macOS），实现开机自启和崩溃自动重启。

用法:
  python3 aim_autostart.py install         注册自启动（当前 Agent）
  python3 aim_autostart.py install --all   注册全部 Agent
  python3 aim_autostart.py remove          移除自启动
  python3 aim_autostart.py status          查看状态
  python3 aim_autostart.py restart         重启服务
"""

import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

AIM_DIR = Path.home() / ".hermes" / "aim"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

AGENTS = {
    "ZS0001": {"name": "呱呱", "framework": "openclaw"},
    "ZS0002": {"name": "吉量", "framework": "hermes"},
    "ZS0003": {"name": "小火鸡儿", "framework": "qwenpaw"},
}


def get_plist_path(agent_id: str) -> Path:
    return LAUNCH_AGENTS_DIR / f"com.aim.agent.{agent_id}.plist"


def create_plist(agent_id: str, framework: str) -> dict:
    """创建 launchd plist 配置"""
    python_path = sys.executable
    script_path = str(AIM_DIR / "aim-agent.py")
    log_path = str(AIM_DIR / "logs" / f"launchd-{agent_id}.log")
    error_log = str(AIM_DIR / "logs" / f"launchd-{agent_id}.error.log")
    
    return {
        "Label": f"com.aim.agent.{agent_id}",
        "ProgramArguments": [
            python_path,
            script_path,
            "--agent-id", agent_id,
            "--framework", framework,
        ],
        "WorkingDirectory": str(AIM_DIR),
        "RunAtLoad": True,           # 开机自启
        "KeepAlive": True,           # 崩溃自动重启
        "StandardOutPath": log_path,
        "StandardErrorPath": error_log,
        "EnvironmentVariables": {
            "no_proxy": "127.0.0.1,localhost",
            "NO_PROXY": "127.0.0.1,localhost",
        },
        "ThrottleInterval": 5,       # 崩溃后等待5秒再重启
    }


def install(agent_id: str, framework: str, name: str):
    """注册自启动"""
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    plist = create_plist(agent_id, framework)
    plist_path = get_plist_path(agent_id)
    
    # 写入 plist
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)
    
    print(f"  📝 已创建: {plist_path}")
    
    # 加载服务
    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True, text=True, timeout=10
    )
    
    if result.returncode == 0:
        print(f"  ✅ {name}({agent_id}) 自启动已注册")
    else:
        print(f"  ❌ 加载失败: {result.stderr.strip()}")


def remove(agent_id: str):
    """移除自启动"""
    plist_path = get_plist_path(agent_id)
    if not plist_path.exists():
        print(f"  ⚠️ {agent_id} 未注册自启动")
        return
    
    # 卸载服务
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, timeout=10)
    plist_path.unlink()
    print(f"  ✅ {agent_id} 自启动已移除")


def status(agent_id: str):
    """查看自启动状态"""
    plist_path = get_plist_path(agent_id)
    
    if not plist_path.exists():
        print(f"  ❌ {agent_id}: 未注册自启动")
        return
    
    # 检查 launchd 状态
    result = subprocess.run(
        ["launchctl", "list", f"com.aim.agent.{agent_id}"],
        capture_output=True, text=True, timeout=10
    )
    
    if result.returncode == 0:
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 3:
            parts = lines[1].split()
            if len(parts) >= 1:
                pid = parts[0]
                status_str = "✅ 运行中" if pid != "-" else "❌ 未运行"
                print(f"  {status_str} {agent_id} (PID: {pid})")
                return
    
    print(f"  ⚠️ {agent_id}: launchd 状态异常")


def main():
    if len(sys.argv) < 2:
        print("AIM agent 自启动管理")
        print()
        print(f"  {sys.argv[0]} install             注册当前 Agent (ZS0002)")
        print(f"  {sys.argv[0]} install --all        注册全部 Agent")
        print(f"  {sys.argv[0]} remove              移除当前 Agent")
        print(f"  {sys.argv[0]} status              查看状态")
        print(f"  {sys.argv[0]} restart             重启服务")
        return
    
    cmd = sys.argv[1]
    
    if cmd == "install":
        all_agents = "--all" in sys.argv
        if all_agents:
            for aid, info in AGENTS.items():
                install(aid, info["framework"], info["name"])
        else:
            install("ZS0002", "hermes", "吉量")
    
    elif cmd == "remove":
        for aid in AGENTS:
            remove(aid)
    
    elif cmd == "status":
        for aid in AGENTS:
            status(aid)
    
    elif cmd == "restart":
        for aid in AGENTS:
            remove(aid)
        time.sleep(2)
        for aid, info in AGENTS.items():
            install(aid, info["framework"], info["name"])


if __name__ == "__main__":
    main()
