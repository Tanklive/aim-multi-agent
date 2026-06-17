#!/usr/bin/env python3
"""
AIM 版本管理 v3 — 只提示不强制

版本号格式: YYYYMMDD.HHMM + 类型后缀 (B/M/P)

原则:
  - Agent 端永远不自改代码，只检测和提示
  - 大版本 (BREAKING): 连接时检测到 → 弹提示，由用户决定
  - 小版本 (MINOR): 发消息时检测到 → 弹建议升级提示
  - 修复 (PATCH): 同小版本
  - 用户确认升级: 运行 aim_version.py upgrade

用法:
  aim_version.py bump --major "说明"   发大版本
  aim_version.py bump --minor "说明"   发小版本  
  aim_version.py bump --patch "说明"   发修复
  aim_version.py check                  检查版本
  aim_version.py upgrade                执行升级（用户触发）
  aim_version.py version                当前版本
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

AIM_DIR = Path.home() / ".hermes" / "aim"
VERSION_FILE = AIM_DIR / "VERSION"
CHANGELOG_FILE = AIM_DIR / "CHANGELOG.md"
AIM_SEND = AIM_DIR / "aim_send.py"
SHARED_VERSION = Path.home() / "shared" / "hub" / "AIM_VERSION"

AGENTS = {
    "ZS0001": {"name": "呱呱", "framework": "openclaw"},
    "ZS0002": {"name": "吉量", "framework": "hermes"},
    "ZS0003": {"name": "小火鸡儿", "framework": "qwenpaw"},
}

SCRIPT = sys.argv[0]


def get_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "0.0.0"


def get_version_type(version: str) -> str:
    """从版本号提取类型标记"""
    # 版本号末尾的字母标记类型: B=大版本, M=小版本, P=修复
    if version.endswith("B"):
        return "BREAKING"
    elif version.endswith("M"):
        return "MINOR"
    elif version.endswith("P"):
        return "PATCH"
    return "PATCH"


def parse_version(ver: str) -> tuple:
    """解析版本号用于比较"""
    base = ver.rstrip("BMP")
    try:
        parts = base.split(".")
        return tuple(int(p) for p in parts)
    except:
        return (0, 0, 0)


def bump_version(vtype: str) -> str:
    """生成新版本号"""
    base = datetime.now().strftime("%Y%m%d.%H%M")
    suffix = {"major": "B", "minor": "M", "patch": "P"}.get(vtype, "M")
    return base + suffix


def write_version(version: str, desc: str, vtype: str):
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(version)

    vtype_names = {"major": "🔴 大版本", "minor": "🟡 小版本", "patch": "🟢 修复"}
    vtype_name = vtype_names.get(vtype, "⚪ 更新")

    CHANGELOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## {version} ({ts}) [{vtype_name}]\n\n{desc}\n"
    if CHANGELOG_FILE.exists():
        content = CHANGELOG_FILE.read_text()
        lines = content.split("\n")
        pos = 0
        for i, line in enumerate(lines):
            if line.startswith("## ") and i > 0:
                pos = i
                break
        if pos > 0:
            lines.insert(pos, entry)
            CHANGELOG_FILE.write_text("\n".join(lines))
        else:
            CHANGELOG_FILE.write_text(content + entry)
    else:
        CHANGELOG_FILE.write_text("# AIM 变更日志\n" + entry)

    SHARED_VERSION.parent.mkdir(parents=True, exist_ok=True)
    SHARED_VERSION.write_text(json.dumps({
        "version": version,
        "type": vtype.upper(),
        "updated": datetime.now().isoformat(),
        "description": desc,
    }, ensure_ascii=False, indent=2))


def push_update_via_aim(version: str, vtype: str, desc: str):
    """通过 AIM 消息推送 [AIM-UPDATE] 指令给所有 Agent"""
    update_json = json.dumps({
        "version": version,
        "type": vtype.upper(),
        "description": desc,
    }, ensure_ascii=False)
    msg = f"[AIM-UPDATE] {update_json}"
    
    print(f"向 {len(AGENTS)} 个 Agent 推送升级指令...")
    for aid, info in AGENTS.items():
        try:
            env = os.environ.copy()
            env["no_proxy"] = "127.0.0.1,localhost"
            r = subprocess.run(
                [sys.executable, str(AIM_SEND), aid, msg, "--from", "ZS0002"],
                capture_output=True, text=True, timeout=15, env=env
            )
            s = "OK" if r.returncode == 0 else "FAIL"
            print(f"  {s} {info['name']}({aid})")
        except Exception as e:
            print(f"  FAIL {info['name']}({aid}): {e}")
        time.sleep(2)
    print("推送完成")


def notify_agents(version: str, desc: str, vtype: str):
    """推送升级通知"""
    vtype_names = {"major": "🔴 大版本", "minor": "🟡 小版本", "patch": "🟢 修复"}
    vtype_name = vtype_names.get(vtype, "更新")

    print(f"向 {len(AGENTS)} 个 Agent 推送 {vtype_name} 通知...")

    for aid, info in AGENTS.items():
        if vtype == "major":
            # 大版本：强制通知，需要手动重启
            msg = (
                f"🔴 AIM 大版本升级: v{version}\n\n"
                f"类型: 通信协议变更\n"
                f"说明: {desc}\n\n"
                f"请尽快重启 aim-agent.py:\n"
                f"pkill -f 'aim-agent.py --agent-id {aid}' && "
                f"cd {AIM_DIR} && "
                f"python3 aim-agent.py --agent-id {aid} --framework {info['framework']}"
            )
        else:
            # 小版本/修复：告知即可，下次使用自动升级
            msg = (
                f"{vtype_name} 可用: v{version}\n"
                f"说明: {desc}\n"
                f"下次连接 AIM 时将自动升级。\n"
                f"或立即执行: python3 aim_version.py auto-upgrade"
            )

        try:
            env = os.environ.copy()
            env["no_proxy"] = "127.0.0.1,localhost"
            r = subprocess.run(
                [sys.executable, str(AIM_SEND), aid, msg, "--from", "ZS0002"],
                capture_output=True, text=True, timeout=15, env=env
            )
            s = "OK" if r.returncode == 0 else "FAIL"
            print(f"  {s} {info['name']}({aid})")
        except Exception as e:
            print(f"  FAIL {info['name']}({aid}): {e}")
        time.sleep(2)
    print("通知完成")


def check_upgrade():
    """检查是否需要升级（Agent端调用）"""
    local = get_version()
    local_ver = parse_version(local)

    if not SHARED_VERSION.exists():
        return {"need_upgrade": False, "reason": "no_remote"}

    try:
        info = json.loads(SHARED_VERSION.read_text())
        remote = info.get("version", "0.0.0")
        vtype = info.get("type", "PATCH")
        desc = info.get("description", "")
    except Exception:
        return {"need_upgrade": False, "reason": "parse_error"}

    remote_ver = parse_version(remote)

    if local == remote:
        return {"need_upgrade": False, "reason": "same_version"}

    if remote_ver > local_ver:
        return {
            "need_upgrade": True,
            "local": local,
            "remote": remote,
            "type": vtype,
            "description": desc,
        }

    return {"need_upgrade": False, "reason": "local_newer"}


def do_upgrade():
    """执行升级（用户触发 — 不自动，由用户决定后手动运行）"""
    print("检查版本...")
    result = check_upgrade()

    if not result.get("need_upgrade"):
        reason = result.get("reason", "")
        if reason == "same_version":
            print(f"版本一致 ({get_version()})，无需升级")
        elif reason == "no_remote":
            print("无服务器版本信息")
        elif reason == "local_newer":
            print(f"本地版本比服务器新")
        else:
            print(f"无需升级: {reason}")
        return

    local = result["local"]
    remote = result["remote"]
    vtype = result["type"]
    desc = result["description"]

    type_names = {"BREAKING": "🔴 大版本", "MINOR": "🟡 小版本", "PATCH": "🟢 修复"}
    type_name = type_names.get(vtype, "更新")

    print(f"\n发现新版本: {local} -> {remote} [{type_name}]")
    print(f"说明: {desc}")
    print()

    # 小版本/修复：自动升级
    if vtype in ("MINOR", "PATCH"):
        print("自动升级中...")
        src = Path.home() / "shared" / "aim"
        if src.exists():
            count = 0
            for f in ["aim-agent.py", "aim_send.py", "aim_sdk.py", "aim_board.py", "VERSION", "CHANGELOG.md"]:
                sf = src / f
                df = AIM_DIR / f
                if sf.exists():
                    df.write_text(sf.read_text())
                    count += 1
            print(f"  已更新 {count} 个文件")
        else:
            print("  共享目录无更新文件，仅更新版本号")

        # 更新本地版本号
        info = json.loads(SHARED_VERSION.read_text())
        write_version(remote, desc, vtype.lower())
        print(f"  版本: {remote}")

        print("\n✅ 升级完成")
        print("建议重启 aim-agent.py 使更新生效")

    else:
        # 大版本：只提示，不自动升级
        print("⚠️ 这是大版本更新，涉及通信协议变更。")
        print("请手动执行:")
        print(f"  pkill -f aim-agent.py")
        print(f"  python3 aim_version.py auto-upgrade")
        print(f"  # 然后重启 aim-agent.py")


def check_and_prompt():
    """Agent 端：检查版本并提示（连接时调用）"""
    result = check_upgrade()

    if not result.get("need_upgrade"):
        return

    remote = result["remote"]
    vtype = result["type"]
    desc = result["description"]

    type_names = {"BREAKING": "🔴 大版本", "MINOR": "🟡 小版本", "PATCH": "🟢 修复"}
    type_name = type_names.get(vtype, "更新")

    if vtype == "BREAKING":
        print(f"\n⚠️  {type_name} 可用: v{remote}")
        print(f"   说明: {desc}")
        print(f"   请尽快重启 aim-agent.py 以应用更新。")
        print()
    else:
        # 小版本/修复：静默升级
        print(f"   {type_name}: v{remote}，自动升级中...")
        do_upgrade()


def check_connection():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(("127.0.0.1", 18900))
        s.close()
        print("AIM Hub: OK")
        return True
    except Exception:
        print("AIM Hub: FAIL")
        return False


def main():
    if len(sys.argv) < 2:
        print(f"AIM 版本管理 v2")
        print(f"  {SCRIPT} bump --major <说明>  大版本（全量推送）")
        print(f"  {SCRIPT} bump --minor <说明>  小版本（按需升级）")
        print(f"  {SCRIPT} bump --patch <说明>  修复（静默升级）")
        print(f"  {SCRIPT} check                 检查版本")
        print(f"  {SCRIPT} auto-upgrade          自动升级")
        print(f"  {SCRIPT} version               当前版本")
        return

    cmd = sys.argv[1]

    if cmd == "bump":
        if len(sys.argv) < 3:
            print("请指定版本类型: --major / --minor / --patch")
            return
        vtype = sys.argv[2].lstrip("-")
        if vtype not in ("major", "minor", "patch"):
            print(f"未知版本类型: {vtype}")
            return
        desc = sys.argv[3] if len(sys.argv) > 3 else "update"
        ver = bump_version(vtype)
        write_version(ver, desc, vtype)
        print(f"版本: v{ver} [{vtype.upper()}]")
        # 无论什么版本类型，都通过 AIM 推送 [AIM-UPDATE] 指令
        # Agent 端收到后会根据 type 决定是否自动升级
        push_update_via_aim(ver, vtype.upper(), desc)

    elif cmd == "check":
        check_connection()
        result = check_upgrade()
        print(f"本地版本: {get_version()}")
        if result.get("need_upgrade"):
            print(f"服务器版本: {result['remote']} [{result['type']}]")
            print(f"说明: {result['description']}")
            print("→ 建议升级")
        else:
            print("版本一致")

    elif cmd == "auto-upgrade":
        do_upgrade()

    elif cmd == "upgrade":
        do_upgrade()

    elif cmd == "version":
        print(f"AIM v{get_version()}")

    elif cmd == "check-and-prompt":
        check_and_prompt()

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
