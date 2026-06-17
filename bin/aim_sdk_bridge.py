#!/usr/bin/env python3
"""aim_sdk_bridge.py — Python SDK 框架通用 Bridge 进程 v1.0

将 Python SDK 框架（CrewAI/AutoGen/LangGraph 等）包装为 AIM Agent。
复用 aim_nats_sdk.py 连接 NATS、收发消息、发射 Observer 事件。

用法：
  python3 aim_sdk_bridge.py --agent-id ZS0004 --framework crewai

支持框架（FRAMEWORKS 字典，新增只需加一个函数）：

新框架接入：在 FRAMEWORKS 字典中添加框架名和对应的调用函数，
  Bridge 主流程不变，不需要改核心代码。

依赖：
  - ~/.aim/bin/aim_nats_sdk.py
  - 目标框架的 Python SDK（如 pip install crewai）
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
#  框架特定的调用函数
#  新增框架只需在此添加一个函数，并在 FRAMEWORKS 注册
# ══════════════════════════════════════════════════════════════


def _call_crewai(text: str) -> str:
    """调用 CrewAI"""
    from crewai import Crew, Agent, Task
    agent = Agent(
        role="assistant",
        goal="helpfully reply to AIM messages",
        backstory="AIM Agent running via CrewAI Bridge",
    )
    task = Task(description=text, agent=agent)
    crew = Crew(agents=[agent], tasks=[task])
    return str(crew.kickoff())


def _call_autogen(text: str) -> str:
    """调用 AutoGen"""
    from autogen import AssistantAgent
    agent = AssistantAgent(name="assistant")
    reply = agent.generate_reply(
        messages=[{"role": "user", "content": text}],
    )
    return str(reply)


# 注册所有支持的框架
FRAMEWORKS = {
    "crewai": _call_crewai,
    "autogen": _call_autogen,
}


# ══════════════════════════════════════════════════════════════
#  Bridge 核心
# ══════════════════════════════════════════════════════════════


class SDKBridge:
    """Python SDK Bridge — 将 SDK 框架包装为 AIM Agent"""

    def __init__(self, agent_id: str, framework: str):
        self.agent_id = agent_id
        self.framework = framework
        self.client = AIMNATSClient.from_config(agent_id)

    async def start(self):
        """启动 Bridge 进程"""
        print(f"🚀 {self.agent_id} SDK Bridge ({self.framework}) 启动...")

        await self.client.connect()
        await self.client.setup_streams()

        # 上线事件
        await self.client.emit_obs(
            "agent_online", "",
            f"{self.agent_id} ({self.framework}) 上线",
        )

        # 订阅消息
        await self.client.subscribe_dm(self._on_dm)
        await self.client.subscribe_grp("grp_trio", self._on_grp)

        # 心跳
        asyncio.create_task(self.client.start_heartbeat(30))

        print(f"✅ {self.agent_id} 就绪，等待消息...")
        await asyncio.Event().wait()

    async def _on_dm(self, envelope: dict, raw_msg):
        """处理私聊消息"""
        await self._process(envelope, is_group=False)

    async def _on_grp(self, envelope: dict, raw_msg):
        """处理群聊消息"""
        if envelope.get("from") == self.agent_id:
            return  # 不自言自语
        await self._process(envelope, is_group=True)

    async def _process(self, envelope: dict, is_group: bool = False):
        """消息处理全流程：emit 事件 → 调用 SDK → 回复"""
        msg_id = envelope.get("id", "")
        payload = envelope.get("payload", {})
        text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        sender = envelope.get("from", "?")

        if not msg_id or not text:
            return

        # 1. received
        await self.client.emit_obs(
            "received", msg_id, f"收到来自 {sender} 的消息",
        )
        # 2. processing
        await self.client.emit_obs(
            "processing", msg_id, "Bridge 处理中",
        )
        # 3. ai_start
        await self.client.emit_obs(
            "ai_start", msg_id, f"调用 {self.framework} SDK",
        )

        try:
            # 调用框架 SDK
            fn = FRAMEWORKS.get(self.framework)
            if not fn:
                raise ValueError(f"不支持的框架: {self.framework}")

            reply = await asyncio.get_event_loop().run_in_executor(None, fn, text)

            if reply:
                # 4. ai_done
                await self.client.emit_obs(
                    "ai_done", msg_id, f"AI 回复: {reply[:80]}",
                )

                # 发送回复
                if is_group:
                    await self.client.send_grp("grp_trio", reply)
                else:
                    await self.client.send_dm(sender, reply)

                # 5. completed
                await self.client.emit_obs(
                    "completed", msg_id, "已回复",
                )
            else:
                await self.client.emit_obs(
                    "ai_empty", msg_id, "SDK 返回空",
                )
        except Exception as e:
            await self.client.emit_obs(
                "error", msg_id, str(e),
            )


# ══════════════════════════════════════════════════════════════
#  CLI 入口
# ══════════════════════════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="AIM SDK Bridge — Python SDK 框架通用 Bridge",
    )
    parser.add_argument("--agent-id", required=True, help="Agent ID (如 ZS0004)")
    parser.add_argument("--framework", required=True,
                        choices=list(FRAMEWORKS.keys()) + ["custom"],
                        help="框架名称")
    args = parser.parse_args()

    bridge = SDKBridge(agent_id=args.agent_id, framework=args.framework)

    try:
        asyncio.run(bridge.start())
    except KeyboardInterrupt:
        print("\n👋 SDK Bridge 已停止")


if __name__ == "__main__":
    main()
