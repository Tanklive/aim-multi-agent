#!/usr/bin/env python3
"""Issue 上报客户端 — 供三方 Agent SDK 调用。

协议（吉量 v1 定稿）：
  Agent → NATS aim.issue.update {agent, level, title, detail, ts}
  Worker → 串行 append → ISSUES.md → git commit

用法：
  from aim_issue_client import report_issue
  await report_issue("ZS0001", "🔴 P0", "adapter 幻听", "dispatch 消费积压旧消息...")
"""

import json
import time
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from aim_nats_sdk import AIMNATSClient

ISSUE_SUBJECT = "aim.issue.update"


async def report_issue(
    agent_id: str,
    level: str,        # 🔴 P0 / 🟡 P1 / 🟢 P2
    title: str,
    detail: str,
    issue_id: str = "",
    creds_path: str = "",
) -> dict:
    """上报问题到统一 ISSUES.md。
    
    Returns:
        {"ok": True} 或 {"ok": False, "error": "..."}
    """
    if not creds_path:
        creds_path = os.path.expanduser(f"~/.aim/agents/{agent_id}/aim.creds")

    try:
        client = AIMNATSClient(
            agent_id=agent_id,
            server="nats://127.0.0.1:4222",
            credentials=creds_path,
        )
        await client.connect()

        payload = json.dumps({
            "agent": agent_id,
            "level": level,
            "title": title,
            "detail": detail,
            "ts": time.time(),
            "issue_id": issue_id,  # 可选，更新已有问题用
        })

        await client.nc.publish(ISSUE_SUBJECT, payload.encode())
        await client.close()
        return {"ok": True}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# CLI 入口（供 shell 脚本调用）
async def main():
    import argparse
    parser = argparse.ArgumentParser(description="上报问题到 PROJECT/ISSUES.md")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--level", required=True, help="🔴 P0 / 🟡 P1 / 🟢 P2")
    parser.add_argument("--title", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--issue-id", default="")
    args = parser.parse_args()

    result = await report_issue(
        args.agent_id, args.level, args.title, args.detail, args.issue_id
    )
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
