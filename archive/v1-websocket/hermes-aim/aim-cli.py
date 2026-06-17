#!/usr/bin/env python3
"""
AIM Agent CLI 入口

通用 CLI 接口，让 AIM Server 通过命令模板调度本 Agent。

用法：
    # 发送消息
    python aim-cli.py send --to ZS0001 --msg "你好"

    # 健康检查
    python aim-cli.py health

    # 执行任务
    python aim-cli.py task --id T001 --desc "搜索 xxx"

    # 查看状态
    python aim-cli.py status

设计文档：~/shared/aim/aim-cli-abstraction.md
"""

import argparse
import json
import sys
import time
from pathlib import Path

# 同目录导入
sys.path.insert(0, str(Path(__file__).parent))


# ── 命令实现 ─────────────────────────

def cmd_send(args):
    """发送消息"""
    # 这里实现实际的消息发送逻辑
    # 对于 OpenClaw Agent，可以通过 sessions_send 发送
    # 对于 Hermes Agent，可以通过 aim_send.py 发送

    print(json.dumps({
        "status": "ok",
        "action": "send",
        "to": args.to,
        "msg": args.msg[:100],  # 截断日志
        "timestamp": int(time.time()),
    }))
    return 0


def cmd_health(args):
    """健康检查"""
    # 检查本 Agent 是否正常运行
    print(json.dumps({
        "status": "ok",
        "healthy": True,
        "timestamp": int(time.time()),
    }))
    return 0


def cmd_task(args):
    """执行任务"""
    print(json.dumps({
        "status": "ok",
        "action": "task",
        "task_id": args.id,
        "desc": args.desc[:100],
        "timestamp": int(time.time()),
    }))
    return 0


def cmd_status(args):
    """查看状态"""
    print(json.dumps({
        "status": "ok",
        "action": "status",
        "timestamp": int(time.time()),
    }))
    return 0


def cmd_watch(args):
    """实时观察客户端状态 (aim watch)"""
    import asyncio
    try:
        from aim_observer import run_observer
    except ImportError:
        print("ERROR: aim_observer.py not found")
        return 1

    print(f"👀 AIM Watch — watching {args.target}")
    print(f"📡 Server: {args.server}")
    if args.verbose:
        print("📝 Verbose mode ON")
    print("─" * 60)

    last_seq = 0
    while True:
        try:
            observer = asyncio.run(run_observer(
                args.server,
                args.agent_id,
                args.target,
                args.verbose,
                last_seq,
            ))
            last_seq = observer if isinstance(observer, int) else 0
        except KeyboardInterrupt:
            print("\n👋 Bye")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}, reconnecting in 3s...")
            time.sleep(3)
    return 0


# ── CLI 入口 ─────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AIM Agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s send --to ZS0001 --msg "你好"
  %(prog)s health
  %(prog)s task --id T001 --desc "搜索 xxx"
  %(prog)s status
        """,
    )

    sub = parser.add_subparsers(dest="command", help="子命令")

    # send
    p_send = sub.add_parser("send", help="发送消息")
    p_send.add_argument("--to", required=True, help="目标 Agent ID")
    p_send.add_argument("--msg", required=True, help="消息内容")

    # health
    sub.add_parser("health", help="健康检查")

    # task
    p_task = sub.add_parser("task", help="执行任务")
    p_task.add_argument("--id", required=True, help="任务 ID")
    p_task.add_argument("--desc", required=True, help="任务描述")

    # status
    sub.add_parser("status", help="查看状态")

    # watch
    p_watch = sub.add_parser("watch", help="实时观察客户端状态")
    p_watch.add_argument("target", help="要 watch 的目标 agent_id (如 ZS0001)")
    p_watch.add_argument("--server", default="ws://127.0.0.1:18900", help="服务端地址")
    p_watch.add_argument("--agent-id", default="observer", help="observer 的 agent_id")
    p_watch.add_argument("--verbose", "-v", action="store_true", help="显示推理摘要")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    cmd_map = {
        "send": cmd_send,
        "health": cmd_health,
        "task": cmd_task,
        "status": cmd_status,
        "watch": cmd_watch,
    }

    return cmd_map[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
