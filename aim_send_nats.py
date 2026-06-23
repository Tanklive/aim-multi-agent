#!/usr/bin/env python3
"""
AIM NATS 消息发送工具 — 基于 aim_nats_sdk.py (Veritas 协议)

维护: ZS0001 (呱呱) | 2026-06-19 起接 | 详见 shared/aim/GOVERNANCE.md

用法:
  python3 aim_send_nats.py ZS0002 "你好" --from ZS0001               # 私聊
  python3 aim_send_nats.py grp_trio "大家好" --group --from ZS0001    # 群聊
  python3 aim_send_nats.py ZS0002 "ping" --request --from ZS0001     # 请求（等待回复）
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 设置 no_proxy 防止代理干扰
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

DEFAULT_NATS_URL = "nats://127.0.0.1:4222"

# 添加 SDK 路径
SDK_DIR = os.path.expanduser("~/.aim/bin")
if os.path.isdir(SDK_DIR) and SDK_DIR not in sys.path:
    sys.path.insert(0, SDK_DIR)

# 也找一下 shared/aim 路径
SHARED_DIR = os.path.expanduser("~/shared/aim")
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

from aim_nats_sdk import AIMNATSClient, make_envelope, make_msg_id


def _get_creds_for(from_id: str) -> str:
    """从 aim.json 获取指定 Agent 的 JWT creds 路径，三级回退"""
    config_path = os.path.expanduser("~/.aim/config/aim.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except Exception:
        return ""

    agents = cfg.get("agents", {})

    # 1. 精确匹配 agents[from_id].creds_path
    if from_id in agents and agents[from_id].get("creds_path"):
        return os.path.expanduser(agents[from_id]["creds_path"])

    # 2. 全局 nats_jwt_path
    jwt_path = cfg.get("nats_jwt_path", "")
    if jwt_path:
        return os.path.expanduser(jwt_path)

    # 3. Token 模式
    return cfg.get("nats_token", "")


async def send_private(from_id: str, to_id: str, content: str, nats_url: str):
    """使用 SDK 发送私聊消息"""
    creds = _get_creds_for(from_id)
    client = AIMNATSClient(from_id, nats_url, credentials=creds)
    await client.connect()
    envelope = await client.send_dm(to_id, content, use_jetstream=True)
    await client.close()
    print(f"✅ [{from_id}] → [{to_id}]: {content[:80]}")
    print(f"   msg_id: {envelope['id']}")
    print(f"   subject: aim.dm.{to_id}")
    return True


async def send_group(from_id: str, group_id: str, content: str, nats_url: str):
    """使用 SDK 发送群聊消息"""
    creds = _get_creds_for(from_id)
    client = AIMNATSClient(from_id, nats_url, credentials=creds)
    await client.connect()
    envelope = await client.send_grp(group_id, content)
    await client.close()
    print(f"✅ [{from_id}] → [群:{group_id}]: {content[:80]}")
    print(f"   msg_id: {envelope['id']}")
    print(f"   subject: aim.grp.{group_id}")
    return True


async def send_request(from_id: str, to_id: str, content: str, timeout: int, nats_url: str):
    """发送请求并等待回复（使用 NATS request-reply）"""
    import nats

    nc = await nats.connect(nats_url)

    # 使用 SDK 的 make_envelope 构造消息
    envelope = make_envelope(from_id, "request", {"text": content})
    subject = f"aim.req.{to_id}"  # aim.req.* 不进入 JetStream

    print(f"📤 发送请求 [{from_id}] → [{to_id}] (timeout={timeout}s)...")
    print(f"   内容: {content[:80]}")

    try:
        response = await nc.request(
            subject,
            json.dumps(envelope, ensure_ascii=False).encode(),
            timeout=timeout,
        )
        data = json.loads(response.data)
        payload = data.get("payload", {})
        reply = payload.get("text", data.get("content", ""))
        print(f"📬 收到回复: {reply[:200]}")
        print(f"   from: {data.get('from', 'unknown')}")

        # 保存到文件
        output = {
            "request": {"from": from_id, "to": to_id, "content": content},
            "response": data,
            "timestamp": datetime.now().isoformat(),
        }
        out_dir = Path.home() / ".hermes" / "aim" / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "nats_requests.jsonl"
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(output, ensure_ascii=False) + "\n")

        await nc.close()
        return data

    except asyncio.TimeoutError:
        print(f"❌ 请求超时 (>{timeout}s)，目标 {to_id} 可能未运行")
        await nc.close()
        return None
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        await nc.close()
        return None


async def main():
    parser = argparse.ArgumentParser(description="AIM NATS 消息发送工具 (Veritas 协议)")
    parser.add_argument("target", help="目标 Agent ID 或群组名")
    parser.add_argument("content", help="消息内容")
    parser.add_argument("--from", dest="from_id", default=None, required=True, help="发送者 ID（必填，ZS0001/ZS0002/ZS0003）")
    parser.add_argument("--group", action="store_true", help="群聊消息")
    parser.add_argument("--request", action="store_true", help="请求模式（等待回复）")
    parser.add_argument("--timeout", type=int, default=10, help="请求超时（秒）")
    parser.add_argument("--nats-url", default=DEFAULT_NATS_URL, help="NATS Server URL")
    args = parser.parse_args()

    if args.request:
        result = await send_request(args.from_id, args.target, args.content, args.timeout, args.nats_url)
        return 0 if result else 1
    elif args.group:
        return 0 if await send_group(args.from_id, args.target, args.content, args.nats_url) else 1
    else:
        return 0 if await send_private(args.from_id, args.target, args.content, args.nats_url) else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
