#!/usr/bin/env python3
"""
AIM Letta 适配器 — 队列监听守护进程 (v1.1)
标准组件，适用于任何使用 Letta Code 框架的 AIM Agent

用法: python3 aim-letta-watcher.py --agent-id ZSxxxx [--queue-dir PATH] [--reply-dir PATH]
或通过环境变量: AIM_AGENT_ID, AIM_QUEUE_DIR, AIM_REPLY_DIR

设计: 2s poll 检测队列变化，有消息即时触发消费者
     空闲时逐步降频到 30s，有消息时立即恢复 2s
"""

import os, sys, time, subprocess, argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="AIM Letta 队列监听器")
    parser.add_argument("--agent-id", default=os.environ.get("AIM_AGENT_ID", ""))
    parser.add_argument("--queue-dir", default=os.environ.get("AIM_QUEUE_DIR", ""))
    parser.add_argument("--reply-dir", default=os.environ.get("AIM_REPLY_DIR", ""))
    parser.add_argument("--consumer-script", default="")
    parser.add_argument("--poll-interval", type=int, default=2)
    parser.add_argument("--cooldown", type=int, default=5)
    parser.add_argument("--idle-max", type=int, default=30)
    args = parser.parse_args()

    if not args.agent_id:
        print("错误: 需要 --agent-id 或 AIM_AGENT_ID 环境变量", file=sys.stderr)
        sys.exit(1)

    agent_id = args.agent_id
    agent_dir = os.path.expanduser(f"~/.aim/agents/{agent_id}")
    log_dir = os.path.join(agent_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "letta-watcher.log")

    # 队列路径：优先参数 > 环境变量 > AIM 标准路径 > 兼容旧路径
    if args.queue_dir:
        queue_dir = os.path.expanduser(args.queue_dir)
    else:
        queue_dir = os.path.expanduser(
            os.environ.get("AIM_QUEUE_DIR",
                os.path.join(agent_dir, "queue"))
        )
    if args.reply_dir:
        reply_dir = os.path.expanduser(args.reply_dir)
    else:
        reply_dir = os.path.expanduser(
            os.environ.get("AIM_REPLY_DIR",
                os.path.join(agent_dir, "replies"))
        )

    # 消费者脚本
    consumer_script = args.consumer_script or os.path.join(agent_dir, "aim-letta-consumer.sh")

    # trigger 文件（nats-agent V2 写入，用于事件通知）
    trigger_file = os.path.join(queue_dir, ".trigger")

    if not os.path.exists(consumer_script):
        print(f"错误: 消费者脚本不存在: {consumer_script}", file=sys.stderr)
        sys.exit(1)

    def log(msg):
        ts = time.strftime("%H:%M:%S")
        try:
            with open(log_file, "a") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    def has_messages():
        try:
            for f in os.listdir(queue_dir):
                if f.endswith('.json'):
                    return True
        except OSError:
            pass
        return False

    def get_queue_mtime():
        try:
            mt = os.path.getmtime(queue_dir)
            if os.path.exists(trigger_file):
                mt = max(mt, os.path.getmtime(trigger_file))
            return mt
        except OSError:
            return 0

    def trigger_consumer():
        try:
            result = subprocess.run(
                ["/bin/bash", consumer_script],
                capture_output=True, text=True, timeout=120,
                env={
                    **os.environ,
                    "PATH": f"/usr/local/bin:/usr/bin:/bin:{os.environ.get('HOME')}/.npm-global/bin:{os.environ.get('PATH','')}",
                    "AIM_QUEUE_DIR": queue_dir,
                    "AIM_REPLY_DIR": reply_dir,
                    "AIM_AGENT_ID": agent_id,
                }
            )
            if result.returncode != 0 and result.stderr.strip():
                log(f"consumer err: {result.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            log("consumer timeout (120s)")
        except Exception as e:
            log(f"trigger error: {e}")

    log(f"启动 agent={agent_id} poll={args.poll_interval}s "
        f"queue={queue_dir} consumer={os.path.basename(consumer_script)}")

    last_mtime = get_queue_mtime()
    last_trigger = 0
    idle_mul = 1

    while True:
        current_mtime = get_queue_mtime()

        if current_mtime > last_mtime:
            last_mtime = current_mtime
            now = time.time()
            if now - last_trigger >= args.cooldown:
                if has_messages():
                    log("检测到新消息 → 触发消费者")
                    trigger_consumer()
                    last_trigger = now
                    idle_mul = 1  # 立即恢复 2s 间隔
            # 冷却中，跳过
            idle_mul = 1
        else:
            # 空闲逐步降频: 2s → 4s → 6s → ... → 30s(max)
            if idle_mul < args.idle_max // args.poll_interval:
                idle_mul += 1

        sleep_time = min(args.poll_interval * idle_mul, args.idle_max)
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
