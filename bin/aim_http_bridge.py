#!/usr/bin/env python3
"""aim_http_bridge.py — HTTP API 框架通用 Bridge 进程 v1.0

将 HTTP API 框架（Dify/Coze/OpenAI Assistants）包装为 AIM Agent。
复用 aim_nats_sdk.py 连接 NATS、收发消息、发射 Observer 事件。

用法：
  python3 aim_http_bridge.py --agent-id ZS0004 --framework dify \\
    --api-url http://localhost:8080/v1/chat-messages --api-key sk-xxx

支持框架（FRAMEWORKS 字典，新增只需加 build/extract 两个 lambda）：


Observer 事件说明：
  HTTP API 返回的是最终结果，没有推理过程可见。
  只发射：received → processing → ai_start → ai_done/ai_empty → completed/error
  不模拟 ai_thinking / ai_tool_call 等中间状态。

依赖：
  - ~/.aim/bin/aim_nats_sdk.py
  - pip install aiohttp
"""

import asyncio
import json
import sys
from pathlib import Path

SDK_DIR = Path.home() / ".aim" / "bin"
if str(SDK_DIR) not in sys.path:
    sys.path.insert(0, str(SDK_DIR))

from aim_nats_sdk import AIMNATSClient

# ══════════════════════════════════════════════════════════════
#  框架配置
#  新增框架只需在 FRAMEWORKS 字典加 build/extract 两项
# ══════════════════════════════════════════════════════════════

FRAMEWORKS = {
    "dify": {
        "build": lambda text: {
            "query": text,
            "response_mode": "blocking",
            "inputs": {},
        },
        "extract": lambda data: data.get("answer", ""),
    },
    "coze": {
        "build": lambda text: {
            "bot_id": "",  # 启动时通过 --bot-id 传入
            "user": "AIM",
            "query": text,
            "conversation_id": "",
        },
        "extract": lambda data: (
            data.get("messages", [{}])[0].get("content", "")
        ),
    },
    "openai": {
        # OpenAI Chat Completions API
        "build": lambda text: {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": text}],
        },
        "extract": lambda data: (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        ),
    },
}

# ══════════════════════════════════════════════════════════════
#  Bridge 核心
# ══════════════════════════════════════════════════════════════


class HTTPAPIBridge:
    """HTTP API Bridge — 将 HTTP API 框架包装为 AIM Agent"""

    def __init__(self, agent_id: str, framework: str,
                 api_url: str, api_key: str = "", bot_id: str = ""):
        self.agent_id = agent_id
        self.framework = framework
        self.api_url = api_url
        self.api_key = api_key
        self.bot_id = bot_id
        self.client = AIMNATSClient.from_config(agent_id)

    async def start(self):
        """启动 Bridge 进程"""
        print(f"🚀 {self.agent_id} HTTP Bridge ({self.framework}) 启动...")
        print(f"   API: {self.api_url}")

        await self.client.connect()
        await self.client.setup_streams()

        # 上线事件
        await self.client.emit_obs(
            "agent_online", "",
            f"{self.agent_id} ({self.framework}) 上线",
        )

        # 订阅消息
        await self.client.subscribe_dm(self._on_dm)

        # 心跳
        asyncio.create_task(self.client.start_heartbeat(30))

        print(f"✅ {self.agent_id} 就绪，等待消息...")
        await asyncio.Event().wait()

    async def _on_dm(self, envelope: dict, raw_msg):
        """处理私聊消息"""
        msg_id = envelope.get("id", "")
        payload = envelope.get("payload", {})
        text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        sender = envelope.get("from", "?")

        if not msg_id or not text:
            return

        # emit 事件链
        await self.client.emit_obs("received", msg_id, f"收到来自 {sender} 的消息")
        await self.client.emit_obs("processing", msg_id, "Bridge 处理中")
        await self.client.emit_obs("ai_start", msg_id, f"调用 {self.framework} API")

        import aiohttp

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        fw = FRAMEWORKS.get(self.framework, {})
        payload_data = fw.get("build", lambda t: {"query": t})(text)

        # 注入 bot_id（Coze 需要）
        if self.framework == "coze" and self.bot_id:
            payload_data["bot_id"] = self.bot_id

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url,
                    json=payload_data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    data = await resp.json()
                    reply = fw.get("extract", lambda d: "")(data)

                    if reply:
                        await self.client.emit_obs(
                            "ai_done", msg_id, f"AI 回复: {reply[:80]}",
                        )
                        await self.client.send_dm(sender, reply)
                        await self.client.emit_obs(
                            "completed", msg_id, "已回复",
                        )
                    else:
                        await self.client.emit_obs(
                            "ai_empty", msg_id, "API 返回空",
                        )
        except asyncio.TimeoutError:
            await self.client.emit_obs("error", msg_id, "HTTP 请求超时")
        except Exception as e:
            await self.client.emit_obs("error", msg_id, str(e))


# ══════════════════════════════════════════════════════════════
#  CLI 入口
# ══════════════════════════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="AIM HTTP Bridge — HTTP API 框架通用 Bridge",
    )
    parser.add_argument("--agent-id", required=True, help="Agent ID (如 ZS0004)")
    parser.add_argument("--framework", required=True,
                        choices=list(FRAMEWORKS.keys()),
                        help="框架名称")
    parser.add_argument("--api-url", required=True, help="HTTP API URL")
    parser.add_argument("--api-key", default="", help="API Key")
    parser.add_argument("--bot-id", default="",
                        help="Bot ID（Coze 需要）")
    args = parser.parse_args()

    bridge = HTTPAPIBridge(
        agent_id=args.agent_id,
        framework=args.framework,
        api_url=args.api_url,
        api_key=args.api_key,
        bot_id=args.bot_id,
    )

    try:
        asyncio.run(bridge.start())
    except KeyboardInterrupt:
        print("\n👋 HTTP Bridge 已停止")


if __name__ == "__main__":
    main()
