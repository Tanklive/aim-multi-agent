#!/usr/bin/env python3
"""
AIM 安装生效流程 — aim install

根据 aim detect 的检测结果，自动配置事件路由。

三阶段：
  1. detect → JSON 检测报告
  2. match  → 选择配置模板
  3. apply  → 根据模板执行具体操作

Usage:
    python3 aim_install.py                              # 自动 detect → install
    python3 aim_install.py --profile hermes-nats         # 手动指定模板
    python3 aim_install.py --dry-run                     # 只展示不执行
    python3 aim_install.py --yes                         # 静默安装
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
AIM_DIR = HOME / ".aim"
BIN_DIR = AIM_DIR / "bin"
CONFIG_DIR = AIM_DIR / "config"
DETECT_FILE = CONFIG_DIR / "detect.json"
PROFILES_DIR = HOME / "shared" / "aim" / "profiles"

# ── 模板定义（内嵌，不依赖外部文件）──

PROFILES = {
    "hermes-nats": {
        "label": "Hermes + NATS",
        "description": "Hermes 框架 + 本地 NATS 长连，适合同机 Agent 集群",
        "actions": [
            {
                "type": "nats_agent",
                "title": "启动 NATS Agent",
                "command": "nats-agent.py --agent-id {agent_id} --framework hermes --nats-url {nats_url}",
                "check": "ps aux | grep nats-agent",
            },
            {
                "type": "webhook",
                "title": "注册 Webhook 路由",
                "command": "hermes webhook subscribe aim-inbound --prompt 'AIM 消息来自 {from}: {content}' --skills aim-message-handler",
                "optional": True,
            },
        ],
    },
    "hermes": {
        "label": "Hermes 纯 Webhook",
        "description": "Hermes 框架，通过 webhook + cron 轮询接收 AIM 消息",
        "actions": [
            {
                "type": "webhook",
                "title": "注册 Webhook 路由",
                "command": "hermes webhook subscribe aim-inbound --prompt 'AIM 消息来自 {from}: {content}' --skills aim-message-handler",
            },
            {
                "type": "cron",
                "title": "创建轮询任务",
                "command": "hermes cron create '* * * * *' --name 'aim-消息轮询' --prompt '检查 AIM 队列消息并处理' --toolsets terminal,file",
            },
        ],
    },
    "openclaw-nats": {
        "label": "OpenClaw + NATS",
        "description": "OpenClaw 守护进程 + NATS 长连",
        "actions": [
            {
                "type": "nats_agent",
                "title": "启动 NATS Agent",
                "command": "nats-agent.py --agent-id {agent_id} --framework openclaw --nats-url {nats_url}",
            },
        ],
    },
    "openclaw-poller": {
        "label": "OpenClaw 轮询",
        "description": "OpenClaw 无 NATS，通过 cron 轮询",
        "actions": [
            {
                "type": "cron",
                "title": "创建轮询任务",
                "command": "hermes cron create '*/30 * * * * *' --name 'aim-消息轮询' --prompt '检查 AIM 队列' --toolsets terminal,file",
            },
        ],
    },
    "letta-local": {
        "label": "Letta 本地模式",
        "description": "Letta Code 本地部署，通过 poll 队列 + launchd watcher 消费 AIM 消息",
        "actions": [
            {
                "type": "handler",
                "title": "安装 aim-letta-adapter",
                "command": "bash install.sh --agent-id {agent_id} --letta-agent-id {letta_agent_id}",
            },
        ],
    },
    "generic-cli": {
        "label": "通用 CLI 型",
        "description": "任意有 CLI 的框架，通过轮询收发消息",
        "actions": [
            {
                "type": "handler",
                "title": "生成默认 handler.sh",
                "command": "generate_handler",
            },
            {
                "type": "cron",
                "title": "创建轮询任务",
                "command": "hermes cron create '*/30 * * * * *' --name 'aim-消息轮询' --prompt '处理 AIM 队列消息' --toolsets terminal,file",
            },
        ],
    },
    "minimal": {
        "label": "最小部署",
        "description": "仅基础消息收发，无事件驱动",
        "actions": [
            {
                "type": "handler",
                "title": "生成默认 handler.sh",
                "command": "generate_handler",
            },
        ],
    },
}


# ── 模板匹配 ──────────────────────────────────────────────────

def match_profile(detect_result: dict) -> str:
    """根据检测结果匹配配置模板"""
    frameworks = detect_result.get("framework", {})
    cli = detect_result.get("cli", {})
    nats = detect_result.get("nats", {})
    http = detect_result.get("http", [])

    installed_frameworks = [
        name for name, info in frameworks.items()
        if info.get("installed")
    ]

    if "hermes" in installed_frameworks:
        if nats.get("available"):
            return "hermes-nats"
        return "hermes"
    elif "openclaw" in installed_frameworks:
        if nats.get("available"):
            return "openclaw-nats"
        return "openclaw-poller"
    elif "letta" in installed_frameworks:
        return "letta-http"
    elif cli:
        return "generic-cli"
    elif http:
        return "generic-http"
    else:
        return "minimal"


# ── 操作执行 ──────────────────────────────────────────────────

def run_command(cmd: str, dry_run: bool = False, timeout: int = 30) -> dict:
    """执行一条命令，返回结果"""
    if dry_run:
        return {"dry_run": True, "command": cmd, "status": "would_execute"}

    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "command": cmd,
            "status": "ok" if r.returncode == 0 else "failed",
            "returncode": r.returncode,
            "stdout": r.stdout.strip()[:200],
            "stderr": r.stderr.strip()[:200],
        }
    except subprocess.TimeoutExpired:
        return {"command": cmd, "status": "timeout", "error": f"超过 {timeout} 秒"}
    except Exception as e:
        return {"command": cmd, "status": "error", "error": str(e)}


def generate_handler(agent_id: str, framework: str = "unknown") -> str:
    """生成默认 handler.sh"""
    AGENTS_DIR = AIM_DIR / "agents"
    agent_dir = AGENTS_DIR / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    handler_path = agent_dir / "handler.sh"

    cli_map = {
        "hermes": "hermes chat -q \"$MESSAGE\" -Q",
        "openclaw": "openclaw agent -m \"$MESSAGE\" --json",
        "letta": "letta agent message --agent main --message \"$MESSAGE\"",
        "claude-code": "claude -p \"$MESSAGE\"",
    }
    cli_cmd = cli_map.get(framework, "echo '请在 handler.sh 中配置你的框架'")

    content = f"""#!/bin/bash
# AIM handler.sh — 自动生成
# 参数1: 发送方 Agent ID
# 参数2: 消息内容
# stdout: 回复内容

SENDER="$1"
MESSAGE="$2"
TIMEOUT="${{AIM_TIMEOUT:-120}}"

# 框架命令（已根据检测结果预填）
timeout $TIMEOUT {cli_cmd}
"""
    handler_path.write_text(content)
    handler_path.chmod(0o755)
    return str(handler_path)


# ── 安装主流程 ──────────────────────────────────────────────

def find_agent_id(detect_result: dict) -> str:
    """找已注册的第一个 Agent ID"""
    agents = detect_result.get("aim", {}).get("agents", [])
    if agents:
        for a in agents:
            aid = a.get("id", "")
            if aid.startswith("ZS"):
                return aid
        return agents[0].get("id", "agent-01")
    return "agent-01"


def get_nats_url(detect_result: dict) -> str:
    """获取 NATS URL"""
    return detect_result.get("nats", {}).get("url", "nats://127.0.0.1:4222")


def get_primary_framework(detect_result: dict) -> str:
    """获取优先级最高的框架名"""
    installed = [
        name for name, info in detect_result.get("framework", {}).items()
        if info.get("installed")
    ]
    for preferred in ["hermes", "openclaw", "letta", "claude-code"]:
        if preferred in installed:
            return preferred
    return installed[0] if installed else "unknown"


def install(detect_result: dict = None, profile: str = None,
            dry_run: bool = False, yes: bool = False):
    """执行安装"""

    # 阶段 1: detect
    if detect_result is None:
        print("🔍 阶段 1/3: 环境检测...")
        # 内联调用 avoid 子进程阻塞
        sys.path.insert(0, str(BIN_DIR))
        import importlib.util
        spec = importlib.util.spec_from_file_location("detect", str(BIN_DIR / "aim_detect.py"))
        detect_mod = importlib.util.module_from_spec(spec)
        old_env = os.environ.get("AIM_DETECT_FAST", "")
        os.environ["AIM_DETECT_FAST"] = "1"
        spec.loader.exec_module(detect_mod)
        if not old_env:
            del os.environ["AIM_DETECT_FAST"]
        detect_result = detect_mod.detect_all()
        detect_result["matched_profile"] = detect_mod.match_profile(detect_result)
        print(f"   框架: {get_primary_framework(detect_result)}")
        print(f"   NATS: {'可用' if detect_result.get('nats',{}).get('available') else '不可用'}")

    # 阶段 2: match
    if profile is None:
        profile = match_profile(detect_result)

    profile_info = PROFILES.get(profile)
    if profile_info is None:
        print(f"❌ 未知模板: {profile}")
        return False

    print(f"\n🔧 阶段 2/3: 匹配模板...")
    print(f"   模板: {profile_info['label']}")
    print(f"   说明: {profile_info['description']}")

    if not yes:
        resp = input(f"\n是否继续安装 {profile_info['label']}？[Y/n]: ").strip().lower()
        if resp == "n" or resp == "no":
            print("⏹ 已取消")
            return False

    # 阶段 3: apply
    print(f"\n🚀 阶段 3/3: 生效配置...")

    agent_id = find_agent_id(detect_result)
    nats_url = get_nats_url(detect_result)
    framework = get_primary_framework(detect_result)
    results = []

    for action in profile_info["actions"]:
        print(f"\n  ▶ {action['title']}")
        cmd = action["command"]

        # 模板变量替换
        cmd = cmd.replace("{agent_id}", agent_id)
        cmd = cmd.replace("{nats_url}", nats_url)

        if action["type"] == "handler" and cmd == "generate_handler":
            path = generate_handler(agent_id, framework)
            print(f"    ✅ 生成 handler: {path}")
            results.append({"action": "handler", "status": "ok", "path": path})
        elif action["type"] == "nats_agent":
            # NATS agent 需要后台启动
            full_cmd = f"cd {BIN_DIR} && nohup python3 {cmd} > /dev/null 2>&1 &"
            if not dry_run:
                r = run_command(full_cmd)
                results.append(r)
                if r["status"] == "ok":
                    print(f"    ✅ 已启动 (后台)")
                else:
                    print(f"    ⚠️ {r.get('stderr', r.get('error', 'unknown'))}")
            else:
                print(f"    🔄 dry-run: {full_cmd[:120]}...")
        else:
            if not dry_run:
                r = run_command(cmd)
                results.append(r)
                print(f"    {'✅' if r['status'] == 'ok' else '⚠️'} {r['status']}: {r.get('stdout', r.get('error', 'ok'))[:100]}")
            else:
                print(f"    🔄 dry-run: {cmd[:120]}...")

    # 保存安装状态
    state = {
        "timestamp": time.time(),
        "profile": profile,
        "agent_id": agent_id,
        "framework": framework,
        "results": results,
        "status": "ok",
    }
    (CONFIG_DIR / "install_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2)
    )

    print(f"\n✅ 安装完成！模板: {profile_info['label']}")
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AIM 安装助手")
    parser.add_argument("--profile", type=str, help="强制指定模板")
    parser.add_argument("--dry-run", action="store_true", help="展示不执行")
    parser.add_argument("--yes", "-y", action="store_true", help="静默安装")
    parser.add_argument("--list-profiles", action="store_true", help="列出可用模板")
    args = parser.parse_args()

    if args.list_profiles:
        print("可用模板:")
        for name, info in PROFILES.items():
            print(f"  {name:20s} {info['label']}")
            print(f"  {'':20s} {info['description']}")
            print()
        return

    install(dry_run=args.dry_run, yes=args.yes, profile=args.profile)


if __name__ == "__main__":
    main()
