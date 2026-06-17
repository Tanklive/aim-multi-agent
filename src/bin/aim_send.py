#!/usr/bin/env python3
"""
AIM NATS 消息发送工具
基于 aim-veritas 协议，通过 NATS 发送消息

Usage:
    # 发送私聊
    aim_send.py --to ZS0001 --text "你好呱呱"

    # 发送群聊
    aim_send.py --grp grp_trio --text "大家好"

    # 使用 JetStream 持久化发送
    aim_send.py --to ZS0001 --text "持久化消息" --jetstream

    # 指定 Agent 身份
    aim_send.py --agent-id ZS0002 --to ZS0001 --text "你好"

Environment:
    AIM_AGENT_ID     — Agent ID (优先级最高)
    AIM_NATS_SERVER  — NATS Server (默认: nats://127.0.0.1:4222)
"""

import asyncio
import json
import os
import sys
import argparse

SDK_DIR = os.path.expanduser("~/.aim/bin")
if SDK_DIR not in sys.path:
    sys.path.insert(0, SDK_DIR)

from aim_nats_sdk import AIMNATSClient


def _auto_detect_agent_id() -> str:
    """自动检测 Agent ID
    优先级: AIM_AGENT_ID 环境变量 > aim.json 配置 > 报错
    """
    # 1. 环境变量
    env_id = os.environ.get("AIM_AGENT_ID", "").strip()
    if env_id:
        return env_id
    # 2. 从 aim.json 读取（取第一个配置了 creds 的 agent）
    try:
        cfg_path = os.path.expanduser("~/.aim/config/aim.json")
        if os.path.exists(cfg_path):
            cfg = json.load(open(cfg_path))
            agents = cfg.get("agents", {})
            for aid, ac in agents.items():
                if ac.get("creds_path"):
                    return aid
    except Exception:
        pass
    return ""


async def main():
    parser = argparse.ArgumentParser(description="AIM NATS 消息发送工具")
    parser.add_argument("--agent-id", default=_auto_detect_agent_id(),
                        help="发送方 Agent ID（自动检测 AIM_AGENT_ID 或 aim.json 配置）")
    parser.add_argument("--server", default=os.environ.get("AIM_NATS_SERVER", "nats://127.0.0.1:4222"),
                        help="NATS Server URL")
    parser.add_argument("--to", help="接收方 Agent ID（私聊）")
    parser.add_argument("--grp", help="群组 ID（群聊）")
    parser.add_argument("--text", required=True, help="消息文本")
    parser.add_argument("--jetstream", action="store_true", help="使用 JetStream 持久化")
    parser.add_argument("--wait", action="store_true", help="等待响应（request-reply）")
    parser.add_argument("--timeout", type=float, default=5.0, help="等待超时（秒）")
    args = parser.parse_args()

    if not args.to and not args.grp:
        print("❌ 必须指定 --to（私聊）或 --grp（群聊）")
        sys.exit(1)

    if not args.agent_id:
        print("❌ 无法确定 Agent ID，请设置 AIM_AGENT_ID 环境变量或传 --agent-id")
        sys.exit(1)

    client = AIMNATSClient.from_config(args.agent_id, server=args.server)
    await client.connect()

    if args.to:
        if args.wait:
            # Request-Reply 模式
            print(f"🔄 发送请求给 {args.to} (等待响应 {args.timeout}s)...")
            try:
                response = await client.send_request(args.to, args.text, timeout=args.timeout)
                print(f"✅ 收到响应: {json.dumps(response, ensure_ascii=False, indent=2)}")
            except Exception as e:
                print(f"⏰ 请求超时或无响应: {e}")
                sys.exit(1)
        else:
            # 普通私聊
            envelope = await client.send_dm(args.to, args.text, use_jetstream=args.jetstream)
            print(f"✅ 私聊已发送: {args.agent_id} → {args.to}")
            print(f"   MsgID: {envelope['id']}")
    else:
        # 群聊
        envelope = await client.send_grp(args.grp, args.text)
        print(f"✅ 群聊已发送: {args.agent_id} → grp:{args.grp}")
        print(f"   MsgID: {envelope['id']}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
