#!/usr/bin/env python3
"""
AIM 环境自检工具 — aim detect

检测本地 Agent 框架环境，输出结构化的能力报告，
供 aim install 自动匹配配置模板。

检测项:
  - framework: 识别已安装的 AI Agent 框架
  - cli: 框架 CLI 可用性
  - nats: NATS 本地可达性
  - http: 本地 HTTP server 检测
  - fs_watch: 文件系统监听能力
  - process_mgr: 进程管理能力
  - aim: AIM 客户端自身状态

输出: JSON 到 stdout，同时写入 ~/.aim/config/detect.json

Usage:
    python3 aim_detect.py                    # 检测并输出JSON
    python3 aim_detect.py --pretty            # 人类可读格式
    python3 aim_detect.py --check nats        # 只检测单项
"""

import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
AIM_DIR = HOME / ".aim"
CONFIG_DIR = AIM_DIR / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
DETECT_FILE = CONFIG_DIR / "detect.json"

# ── 框架检测 ──────────────────────────────────────────────────

FRAMEWORK_CHECKS = {
    "hermes": {
        "env_var": None,
        "cli_cmd": ["hermes", "--version"],
        "paths": [
            HOME / ".hermes" / "config.yaml",
            HOME / ".hermes" / "hermes-agent",
        ],
        "markers": [
            (HOME / ".hermes" / "config.yaml", "model:"),
            (HOME / ".hermes" / "skills", None),
        ],
    },
    "openclaw": {
        "env_var": "OPENCLAW_HOME",
        "cli_cmd": ["openclaw", "--version"],
        "paths": [
            HOME / ".openclaw",
        ],
        "markers": [],
    },
    "letta": {
        "env_var": "LETTA_HOME",
        "cli_cmd": ["letta", "--version"],
        "cli_timeout": 8,
        "paths": [
            HOME / ".letta",
        ],
        "markers": [],
    },
    "claude-code": {
        "env_var": None,
        "cli_cmd": ["claude", "--version"],
        "paths": [],
        "markers": [],
    },
    "copilot": {
        "env_var": None,
        "cli_cmd": ["copilot", "--version"],
        "paths": [],
        "markers": [],
    },
}


def detect_framework() -> dict:
    """检测已安装的 AI Agent 框架，返回 {name: confidence}"""
    if os.environ.get("AIM_DETECT_FAST"):
        return {}

    results = {}
    for name, check in FRAMEWORK_CHECKS.items():
        confidence = 0
        reasons = []

        # 1. 环境变量
        if check["env_var"] and os.environ.get(check["env_var"]):
            confidence += 30
            reasons.append(f"ENV:{check['env_var']}")

        # 2. CLI 命令
        cli_timeout = check.get("cli_timeout", 3)
        try:
            r = subprocess.run(
                check["cli_cmd"], capture_output=True, text=True,
                timeout=cli_timeout,
            )
            if r.returncode == 0:
                confidence += 50
                reasons.append(f"CLI:{' '.join(check['cli_cmd'])} OK")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # 3. 路径检测
        for p in check["paths"]:
            if p.exists():
                confidence += 20
                reasons.append(f"PATH:{p}")

        # 4. 内容标记
        for path, marker in check["markers"]:
            if path.exists() and marker:
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    if marker in content:
                        confidence += 15
                        reasons.append(f"MARKER:{path}:{marker}")
                except Exception:
                    pass

        if confidence > 0:
            results[name] = {
                "confidence": min(confidence, 100),
                "reasons": reasons,
                "installed": confidence >= 50,
            }
    return results


# ── CLI 可用性检测 ────────────────────────────────────────────

def detect_cli() -> dict:
    """检测系统中有哪些可用的 Agent CLI 命令"""
    clis = ["hermes", "openclaw", "letta", "claude", "copilot"]
    available = {}
    for cmd in clis:
        try:
            r = subprocess.run(
                ["which", cmd], capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0 and r.stdout.strip():
                path = r.stdout.strip()
                # 拿版本
                ver_output = ""
                for flag in ["--version", "version", "-v"]:
                    try:
                        vr = subprocess.run(
                            [cmd, flag], capture_output=True, text=True, timeout=3
                        )
                        if vr.returncode == 0:
                            ver_output = vr.stdout.strip().split("\n")[0][:80]
                            break
                    except Exception:
                        continue
                available[cmd] = {"path": path, "version": ver_output or "unknown"}
        except FileNotFoundError:
            continue
    return available


# ── NATS 可达性检测 ──────────────────────────────────────────

def detect_nats() -> dict:
    """检测本地 NATS Server 是否可达"""
    result = {
        "available": False,
        "url": None,
        "server_process": False,
        "jetstream": False,
        "error": None,
    }

    # 1. 尝试本地默认地址
    servers = [
        ("nats://127.0.0.1:4222", "default"),
        ("nats://localhost:4222", "localhost"),
    ]

    # 2. 从 AIM 配置读取
    aim_config = AIM_DIR / "config" / "aim.json"
    if aim_config.exists():
        try:
            cfg = json.loads(aim_config.read_text())
            if "nats_url" in cfg:
                servers.insert(0, (cfg["nats_url"], "config"))
        except Exception:
            pass

    # 3. 尝试连接（无依赖检测，只尝试 TCP 连接）
    for url, source in servers:
        try:
            # 解析地址
            addr = url.replace("nats://", "").replace("tls://", "")
            host, port_str = addr.split(":")
            port = int(port_str)

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((host, port))
            sock.close()

            result["available"] = True
            result["url"] = url
            result["source"] = source

            # 4. 检查是否有 NATS server 进程
            try:
                ps = subprocess.run(
                    ["pgrep", "-f", "nats-server"],
                    capture_output=True, text=True, timeout=3
                )
                if ps.returncode == 0:
                    result["server_process"] = True
            except Exception:
                pass

            break
        except Exception as e:
            result["error"] = str(e)
            continue

    return result


# ── HTTP Server 检测 ──────────────────────────────────────────

# 常见 AI Agent 框架的默认 HTTP 端口
HTTP_CHECKS = {
    "letta": {"port": 8283, "path": "/", "label": "Letta API"},
    "openai": {"port": 8080, "path": "/v1/models", "label": "LocalAI/OpenAI API"},
    "ollama": {"port": 11434, "path": "/api/tags", "label": "Ollama API"},
    "aim-observer": {"port": 18901, "path": "/health", "label": "AIM Observer"},
    "custom": {"port": 8000, "path": "/", "label": "Generic HTTP"},
}


def detect_http() -> list:
    """检测本地 HTTP 服务端口"""
    found = []
    for name, check in HTTP_CHECKS.items():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", check["port"]))
            sock.close()
            if result == 0:
                found.append({
                    "name": name,
                    "port": check["port"],
                    "label": check["label"],
                })
        except Exception:
            continue
    return found


# ── 文件系统监听能力检测 ──────────────────────────────────────

def detect_fs_watch() -> dict:
    """检测文件系统监听能力"""
    system = platform.system()
    result = {
        "available": False,
        "mechanism": None,
        "libraries": [],
    }

    if system == "Darwin":
        # macOS: 有 FSEvents (原生)
        result["available"] = True
        result["mechanism"] = "fsevents"
        result["libraries"] = ["CoreServices (built-in)"]

        # 检查 PyObjC (Python 绑定的 FSEvents)
        try:
            import objc  # noqa: F401
            result["libraries"].append("PyObjC")
        except ImportError:
            pass

    elif system == "Linux":
        # Linux: 检查 inotify
        if os.path.exists("/proc/sys/fs/inotify"):
            result["available"] = True
            result["mechanism"] = "inotify"

        try:
            import inotify_simple  # noqa: F401
            result["libraries"].append("inotify_simple")
        except ImportError:
            try:
                import pyinotify  # noqa: F401
                result["libraries"].append("pyinotify")
            except ImportError:
                pass

    # 即使没有原生机制，也可以通过 cron 轮询降级
    result["fallback"] = "poll"
    result["fallback_label"] = "cron 轮询降级 (每 N 秒/分检查)"

    return result


# ── 进程管理检测 ──────────────────────────────────────────────

def detect_process_mgr() -> dict:
    """检测可用的进程管理工具"""
    result = {
        "available": [],
        "recommended": None,
    }

    system = platform.system()

    if system == "Darwin":
        result["available"].append({
            "name": "launchd",
            "check": "launchctl list >/dev/null 2>&1",
            "config_dir": "~/Library/LaunchAgents/",
        })
        result["recommended"] = "launchd"

    if system == "Linux":
        result["available"].append({
            "name": "systemd",
            "check": "systemctl --user >/dev/null 2>&1",
            "config_dir": "~/.config/systemd/user/",
        })
        result["recommended"] = "systemd"

    # 通用: screen / tmux / supervisor
    for mgr in ["screen", "tmux", "supervisord"]:
        try:
            r = subprocess.run(
                ["which", mgr], capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                result["available"].append({
                    "name": mgr,
                    "path": r.stdout.strip(),
                })
                if result["recommended"] is None:
                    result["recommended"] = mgr
        except Exception:
            continue

    # 如果 Hermes gateway 本身支持进程管理
    try:
        r = subprocess.run(
            ["hermes", "gateway", "status"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 or "status" in r.stdout.lower():
            result["available"].append({
                "name": "hermes-gateway",
                "note": "Hermes 内置 gateway 进程管理",
            })
    except Exception:
        pass

    return result


# ── AIM 自身状态检测 ──────────────────────────────────────────

def detect_aim() -> dict:
    """检测 AIM 客户端自身安装状态"""
    result = {
        "installed": False,
        "version": None,
        "bin_files": [],
        "agents": [],
        "registered": False,
    }

    bin_dir = AIM_DIR / "bin"
    if bin_dir.exists():
        result["installed"] = True
        result["bin_files"] = sorted(
            f.name for f in bin_dir.iterdir()
            if f.is_file() and not f.name.endswith(".pyc")
        )

    # 检测已注册的 Agent
    agents_dir = AIM_DIR / "agents"
    if agents_dir.exists():
        for d in sorted(agents_dir.iterdir()):
            if d.is_dir():
                identity = d / "identity.json"
                if identity.exists():
                    try:
                        info = json.loads(identity.read_text())
                        result["agents"].append(info)
                        result["registered"] = True
                    except Exception:
                        result["agents"].append({"id": d.name, "error": "bad identity"})
                else:
                    result["agents"].append({"id": d.name, "status": "no identity"})

    return result


# ── 系统信息 ──────────────────────────────────────────────────

def detect_system() -> dict:
    """系统基本信息"""
    return {
        "platform": platform.system(),
        "platform_version": platform.version(),
        "python": platform.python_version(),
        "hostname": platform.node(),
        "arch": platform.machine(),
    }


# ── 主入口 ──────────────────────────────────────────────────

def detect_all() -> dict:
    """执行全部检测"""
    return {
        "timestamp": time.time(),
        "system": detect_system(),
        "framework": detect_framework(),
        "cli": detect_cli(),
        "nats": detect_nats(),
        "http": detect_http(),
        "fs_watch": detect_fs_watch(),
        "process_mgr": detect_process_mgr(),
        "aim": detect_aim(),
    }


def match_profile(detect_result: dict) -> str:
    """根据检测结果匹配配置模板

    优先级：
      1. target_agent 指定 → 按指定的 framework 匹配
      2. 多框架共存时，以安装时 `--framework` 或 detect.json 中的 target_framework 为准
      3. 兜底按机器已安装框架优先级匹配
    """
    frameworks = detect_result.get("framework", {})
    cli = detect_result.get("cli", {})
    nats = detect_result.get("nats", {})
    http = detect_result.get("http", [])
    target = detect_result.get("target_agent", "")

    # 指定了目标 framework → 精准匹配
    if target:
        if target == "letta":
            # Letta 本地模式（无 HTTP API 8283 时）
            return "letta-local"
        elif target == "hermes":
            return "hermes-nats" if nats.get("available") else "hermes"
        elif target == "openclaw":
            return "openclaw-nats" if nats.get("available") else "openclaw-poller"
        elif target == "claude-code":
            return "generic-cli"

    installed_frameworks = [
        name for name, info in frameworks.items()
        if info.get("installed")
    ]

    if "hermes" in installed_frameworks:
        return "hermes-nats" if nats.get("available") else "hermes"
    elif "openclaw" in installed_frameworks:
        return "openclaw-nats" if nats.get("available") else "openclaw-poller"
    elif "letta" in installed_frameworks:
        return "letta-local"
    elif cli:
        return "generic-cli"
    elif http:
        return "generic-http"
    else:
        return "minimal"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AIM 环境自检工具")
    parser.add_argument("--pretty", action="store_true", help="人类可读输出")
    parser.add_argument("--check", type=str, help="只检测单项: framework/cli/nats/http/fs/proc/aim")
    parser.add_argument("--save", action="store_true", help="保存到 detect.json (默认)")
    parser.add_argument("--no-save", action="store_true", help="不保存")
    args = parser.parse_args()

    # 单项检测
    check_map = {
        "framework": detect_framework,
        "cli": detect_cli,
        "nats": detect_nats,
        "http": detect_http,
        "fs": detect_fs_watch,
        "proc": detect_process_mgr,
        "aim": detect_aim,
    }

    if args.check:
        if args.check in check_map:
            result = {args.check: check_map[args.check]()}
        else:
            print(f"未知检测项: {args.check}，可选: {','.join(check_map.keys())}")
            sys.exit(1)
    else:
        result = detect_all()
        result["matched_profile"] = match_profile(result)

    # 输出
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))

    # 保存
    if not args.no_save and not args.check:
        DETECT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    sys.exit(0)


if __name__ == "__main__":
    main()
