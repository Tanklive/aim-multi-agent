#!/usr/bin/env python3
"""
AIM — 统一入口
任何节点都能作为服务端+客户端运行

用法:
  python3 jlm.py start                启动节点（服务端+客户端）
  python3 jlm.py stop                 停止节点
  python3 jlm.py restart              重启
  python3 jlm.py status               查看状态
  python3 jlm.py send <to> <msg>      发送单聊消息
  python3 jlm.py send-group <gid> <msg>  发送群消息
  python3 jlm.py online               查看在线Agent
  python3 jlm.py logs [n]             查看日志

配置文件: config.json
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
PID_FILE = BASE_DIR / "data" / "server.pid"
CONFIG_FILE = BASE_DIR / "config.json"
NODE_SCRIPT = BASE_DIR / "node.py"
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python3"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def get_node_id():
    return load_config().get("node_id", "ZS0002")


def get_pid():
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except:
            pass
    return None


def is_running():
    pid = get_pid()
    if pid:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            pass
    return False


def cmd_start(args):
    if is_running():
        print(f"AIM 已在运行 (PID: {get_pid()})")
        return

    log_file = BASE_DIR / "logs" / "server.log"
    log_file.parent.mkdir(exist_ok=True)

    node_id = args[0] if args else get_node_id()

    proc = subprocess.Popen(
        [PYTHON, str(NODE_SCRIPT)],
        cwd=str(BASE_DIR),
        stdout=open(log_file, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "JLM_NODE_ID": node_id},
    )

    PID_FILE.write_text(str(proc.pid))
    time.sleep(1)

    if proc.poll() is None:
        config = load_config()
        agent_cfg = config["agents"].get(node_id, {})
        port = agent_cfg.get("port", 18900)
        print(f"✅ AIM 节点已启动")
        print(f"   PID: {proc.pid}")
        print(f"   身份: {agent_cfg.get('emoji','')}{agent_cfg.get('name','')}({node_id})")
        print(f"   监听: ws://127.0.0.1:{port}")
        print(f"   日志: {log_file}")
    else:
        print(f"❌ 启动失败，检查日志: {log_file}")
        PID_FILE.unlink(missing_ok=True)


def cmd_stop():
    pid = get_pid()
    if not pid:
        print("AIM 未运行")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        print(f"✅ AIM 已停止 (PID: {pid})")
    except OSError:
        print("进程已不存在")
    PID_FILE.unlink(missing_ok=True)


def cmd_restart(args):
    cmd_stop()
    time.sleep(1)
    cmd_start(args)


def cmd_status():
    pid = get_pid()
    if is_running():
        config = load_config()
        node_id = config.get("node_id", "ZS0002")
        my_cfg = config["agents"].get(node_id, {})
        port = my_cfg.get("port", 18900)
        agents_str = ", ".join(
            f"{v.get('emoji','')}{v.get('name','')}({k})"
            for k, v in config["agents"].items()
        )
        print(f"🟢 AIM 运行中")
        print(f"   PID: {pid}")
        print(f"   身份: {my_cfg.get('emoji','')}{my_cfg.get('name','')}({node_id})")
        print(f"   监听: ws://127.0.0.1:{port}")
        print(f"   Agent: {agents_str}")
        groups = config.get("groups", {})
        if groups:
            for gid, g in groups.items():
                print(f"   群组: {g['name']}({gid})")
    else:
        print(f"🔴 AIM 未运行")


def cmd_send(args, group=False):
    if len(args) < 2:
        print(f"用法: jlm.py send <to_id> <message>")
        print(f"      jlm.py send-group <group_id> <message>")
        print(f"选项: --node-id <id>  指定发送者身份")
        return

    # 解析 --node-id 参数
    node_id = None
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--node-id" and i + 1 < len(args):
            node_id = args[i + 1]
            i += 2
        else:
            filtered_args.append(args[i])
            i += 1

    if len(filtered_args) < 2:
        print(f"用法: jlm.py send <to_id> <message> [--node-id <id>]")
        return

    to_id = filtered_args[0]
    content = " ".join(filtered_args[1:])
    cmd_args = [PYTHON, str(NODE_SCRIPT)]
    if node_id:
        cmd_args += ["--node-id", node_id]
    if group:
        cmd_args += ["--send-group", to_id, content]
    else:
        cmd_args += ["--send", to_id, content]

    result = subprocess.run(
        cmd_args, capture_output=True, text=True, timeout=15,
        cwd=str(BASE_DIR),
    )
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0 and result.stderr:
        print(f"错误: {result.stderr.strip()}")


def cmd_online():
    result = subprocess.run(
        [PYTHON, str(NODE_SCRIPT), "--online"],
        capture_output=True, text=True, timeout=15,
        cwd=str(BASE_DIR),
    )
    if result.stdout:
        print(result.stdout.strip())
    else:
        print("无响应或无在线Agent")


def cmd_logs(n=30):
    log_file = BASE_DIR / "logs" / "server.log"
    if not log_file.exists():
        print("无日志")
        return
    lines = log_file.read_text().strip().split("\n")
    for line in lines[-n:]:
        print(line)


def cmd_help():
    print(__doc__)


def main():
    if len(sys.argv) < 2:
        cmd_help()
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "start": lambda: cmd_start(args),
        "stop": cmd_stop,
        "restart": lambda: cmd_restart(args),
        "status": cmd_status,
        "send": lambda: cmd_send(args),
        "send-group": lambda: cmd_send(args, group=True),
        "online": cmd_online,
        "logs": lambda: cmd_logs(int(args[0]) if args else 30),
        "help": cmd_help,
    }

    handler = commands.get(cmd)
    if handler:
        handler()
    else:
        print(f"未知命令: {cmd}")
        cmd_help()


if __name__ == "__main__":
    main()
