#!/usr/bin/env python3
"""
!! 废弃 !! — 此文件使用旧消息格式，不兼容 Veritas 协议
!! 请使用 ~/.aim/agents/ZS0002/nats-agent.py (基于 aim_nats_sdk.py 的 Veritas 实现)
!! 保留以供参考，不再运行

AIM Agent NATS Adapter — Phase 1 核心链路 (SDK 集成版)
整合现有 aim-agent.py 的 AI 处理逻辑到 NATS 传输层

架构：
  AIMAgentNatsAdapter (NATS 传输 + AI 处理)
  ├── AIMNATSClient (统一 SDK — 连接 / 订阅 / 发送 / Pin / Retry)
  ├── FrameworkCLI (AI 调用 — Hermes / OpenClaw / Letta)
  └── 日志 / 心跳 / 自动重连 / 归档

协议对齐 (Veritas v1.0):
  - SDK 侧: aim_nats_sdk.py (AIMNATSClient + AIMPin + RetryManager)
  - 消息信封: {"ver","id","ts","from","type","payload":{"text":...}}
  - Subject: aim.dm.<id> / aim.grp.<id> / aim.req.<id>
  - 呱呱建议: adapter 只包 SDK 4 个方法 + 消息格式转换
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ── 路径配置 ────────────────────────────

BASE_DIR = Path(__file__).parent
AIM_BASE = Path.home() / ".hermes" / "aim"
LOG_DIR = AIM_BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(AIM_BASE))

# ── 日志 ────────────────────────────────


def setup_logging(name: str, agent_id: str, log_level: int = logging.DEBUG):
    """配置日志"""
    log_file = LOG_DIR / f"nats-agent-{agent_id}.log"
    log = logging.getLogger(name)
    log.setLevel(log_level)

    if not log.handlers:
        fh = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
        log.addHandler(fh)

        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.INFO)
        log.addHandler(ch)

    return log


# ── 消息归档 ────────────────────────────


class MessageArchive:
    """消息归档到 JSONL"""

    def __init__(self, agent_id: str, logger: logging.Logger):
        self.data_dir = AIM_BASE / "data"
        self.log = logger
        self.data_dir.mkdir(exist_ok=True)
        self.file = self.data_dir / f"nats_messages_{agent_id}.jsonl"

    def archive(self, msg: dict):
        try:
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            self.log.warning(f"归档失败: {e}")


# ── Veritas SDK 导入 ───────────────────


def _import_sdk():
    """导入 Veritas SDK（延迟 import 避免循环）"""
    try:
        from aim_nats_sdk import AIMNATSClient
        return AIMNATSClient
    except ImportError:
        # 备选路径
        sys.path.insert(0, str(BASE_DIR))
        from aim_nats_sdk import AIMNATSClient
        return AIMNATSClient


# ── AIM Agent NATS Adapter ──────────────


class AIMAgentNatsAdapter:
    """
    AIM Agent NATS Adapter — Phase 1 核心链路 (SDK 集成版)
    使用 aim_nats_sdk.py (Veritas 协议 v1.0) 作为统一传输层。

    SDK 内置 Pin 去重 + RetryManager，adapter 专注消息格式转换和 AI 处理。
    """

    MAX_CONCURRENT = 3
    AI_TIMEOUT_DEFAULT = 120
    AI_TIMEOUT_SHORT = 180
    AI_TIMEOUT_LONG = 300
    HEARTBEAT_INTERVAL = 300

    # 默认群聊列表
    DEFAULT_GROUPS = ["grp_trio"]

    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        framework: str = "hermes",
        nats_url: str = "nats://127.0.0.1:4222",
        emoji: str = "🤖",
    ):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.framework = framework
        self.nats_url = nats_url
        self.emoji = emoji

        # 日志
        self.log = setup_logging("aim-nats-adapter", agent_id)

        # ── Veritas SDK (内置 Pin + Retry) ──
        AIMNATSClientCls = _import_sdk()
        self.client = AIMNATSClientCls.from_config(
            agent_id=agent_id,
            server=nats_url,
        )
        self.log.info(f"✅ A lot more... Veritas SDK initialized: agent_id={agent_id}")

        # 归档
        self.archive = MessageArchive(agent_id, self.log)

        # 并发控制
        self.semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._active_tasks: Dict[str, asyncio.Task] = {}

        # AI 处理
        self._cli_paths = self._load_cli_paths()
        self._commands = self._load_commands_config()

        # 运行状态
        self._running = False
        self._last_reply_time = 0
        self._reply_cooldown = 3

        # 群聊列表
        self._initial_subscribed_grps: List[str] = []

    # ── 配置加载 ─────────────────────────

    def _load_config(self) -> dict:
        config_file = AIM_BASE / "config.json"
        if config_file.exists():
            try:
                return json.loads(config_file.read_text())
            except Exception as e:
                self.log.warning(f"配置文件读取失败: {e}")
        return {}

    def _load_cli_paths(self) -> Dict[str, str]:
        config = self._load_config()
        paths = {}
        agents = config.get("agents", {})
        for aid, info in agents.items():
            if isinstance(info, dict) and "cli" in info:
                paths[aid] = os.path.expanduser(info["cli"])
        defaults = {
            "hermes": os.path.expanduser("~/.hermes/hermes-agent/.venv/bin/hermes"),
            "openclaw": os.path.expanduser("~/.npm-global/bin/openclaw"),
            "letta": "letta",
        }
        for aid, cli in defaults.items():
            if aid not in paths:
                paths[aid] = cli
        return paths

    def _load_commands_config(self) -> dict:
        config = self._load_config()
        return config.get("commands", {})

    # ── AI 处理 ─────────────────────────

    async def _call_ai(self, prompt: str, timeout: Optional[int] = None) -> str:
        """调用 AI 处理消息"""
        if timeout is None:
            timeout = self.AI_TIMEOUT_DEFAULT

        try:
            from framework_cli import FrameworkCLI
            fw = FrameworkCLI(self.framework, self._commands, self._cli_paths)
            from ai_types import AIRequest, AIResponse
            request = AIRequest(prompt=prompt, agent_id=self.agent_id, timeout=timeout)
            response: AIResponse = await fw.call(request)
            if response.success and response.text:
                return response.text.strip()
            self.log.warning(f"AI 调用未返回内容: {response.error}")
        except Exception as e:
            self.log.warning(f"FrameworkCLI 不可用，降级: {e}")

        return await self._fallback_call_ai(prompt, timeout)

    async def _fallback_call_ai(self, prompt: str, timeout: int) -> str:
        """降级：直接调用框架 CLI"""
        import subprocess

        cli_path = self._cli_paths.get(self.framework, self.framework)
        cmd = [cli_path, "chat", "-q", prompt, "-Q"]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=os.environ,
            )
            if result.returncode == 0:
                output = result.stdout.strip()
                lines = [l for l in output.split("\n") if not l.startswith("⚠️")]
                return "\n".join(lines).strip()
            else:
                self.log.error(f"CLI 调用失败 (rc={result.returncode}): {result.stderr}")
        except subprocess.TimeoutExpired:
            self.log.error(f"AI 调用超时 ({timeout}s)")
        except FileNotFoundError:
            self.log.error(f"CLI 未找到: {cli_path}")
        except Exception as e:
            self.log.error(f"AI 调用异常: {e}")

        return ""

    # ── 消息处理 ─────────────────────────

    def _envelope_to_internal(self, envelope: dict) -> dict:
        """将 Veritas 信封转为内部处理格式"""
        payload = envelope.get("payload", {})
        content = ""
        if isinstance(payload, dict):
            content = payload.get("text", "")
        elif isinstance(payload, str):
            content = payload

        return {
            "msg_id": envelope.get("id", ""),
            "type": "dm" if envelope.get("type") in ("dm", "request") else envelope.get("type", "dm"),
            "from": envelope.get("from", ""),
            "content": content,
            "timestamp": envelope.get("ts", datetime.now(timezone.utc).isoformat()),
            # 保留原始信封中可能有的元信息
            "metadata": {"reply_to": envelope.get("meta", {}).get("reply_to", "")},
        }

    def _internal_to_envelope(self, internal: dict, reply_to_id: str = "") -> dict:
        """将内部消息转为 Veritas 信封（用于回复）"""
        from aim_nats_sdk import make_envelope
        return make_envelope(
            from_id=self.agent_id,
            msg_type="dm",
            payload={"text": internal.get("content", "")},
            reply_to=reply_to_id,
            msg_id=internal.get("msg_id", ""),
        )

    async def handle_envelope(self, envelope: dict):
        """处理 Veritas 信封消息（SDK 已做 Pin 去重）"""
        internal = self._envelope_to_internal(envelope)
        msg_id = internal["msg_id"]
        content = internal["content"]
        from_id = internal["from"]

        if not content:
            return

        # 不自言自语
        if from_id == self.agent_id:
            return

        self.log.info(f"📨 [{internal['type']}] {from_id}: {content[:80]}")

        # 归档
        self.archive.archive(envelope)

        # 并发处理
        task = asyncio.create_task(self._process_internal(internal, envelope))
        self._active_tasks[msg_id] = task
        task.add_done_callback(lambda t: self._active_tasks.pop(msg_id, None))

    async def _process_internal(self, internal: dict, original_envelope: dict):
        """处理内部格式消息（AI 调用 + 回复）"""
        msg_id = internal["msg_id"]
        msg_type = internal["type"]
        from_id = internal["from"]
        content = internal["content"]

        async with self.semaphore:
            try:
                # 调用 AI
                prompt = f"[{from_id}]: {content}" if msg_type == "dm" else f"[{from_id} 群聊]: {content}"
                reply_text = await self._call_ai(prompt)

                if not reply_text:
                    self.log.warning(f"⚠️ AI 未生成回复，跳过")
                    return

                self.log.info(f"✍️ 回复 {from_id}: {reply_text[:60]}...")

                # 通过 SDK 发送回复（含内置 RetryManager）
                if msg_type == "dm":
                    reply_to_id = original_envelope.get("id", "")
                    await self.client.send_dm(
                        to_id=from_id,
                        text=reply_text,
                        reply_to=reply_to_id,
                        enable_retry=True,
                    )
                    self.log.info(f"✅ 已回复 {from_id}")
                elif msg_type == "grp" or msg_type == "group":
                    group_id = original_envelope.get("payload", {}).get("group", "")
                    if not group_id:
                        # 从 subject 推断
                        subject = original_envelope.get("_subject", "")
                        if "grp." in subject:
                            group_id = subject.split("grp.")[-1]
                    if not group_id:
                        group_id = "grp_trio"  # 默认
                    await self.client.send_grp(
                        group_id=group_id,
                        text=reply_text,
                        enable_retry=True,
                    )
                    self.log.info(f"✅ 已回复群聊 {group_id}")

            except Exception as e:
                self.log.error(f"❌ 处理消息失败: {e}")
                import traceback
                self.log.error(traceback.format_exc())

    # ── SDK 回调 ─────────────────────────

    async def _on_dm(self, envelope: dict, raw_msg):
        """SDK 私聊回调（SDK 已做 Pin 去重）"""
        self.log.debug(f"< DM: {envelope.get('from')} → {envelope.get('payload', {}).get('text', '')[:60]}")
        # 附加 raw subject 信息
        envelope["_subject"] = raw_msg.subject if hasattr(raw_msg, 'subject') else ""
        asyncio.create_task(self.handle_envelope(envelope))

    async def _on_grp(self, envelope: dict, raw_msg):
        """SDK 群聊回调（SDK 已做 Pin 去重）"""
        self.log.debug(f"< Group: {envelope.get('from')} → {envelope.get('payload', {}).get('text', '')[:60]}")
        envelope["_subject"] = raw_msg.subject if hasattr(raw_msg, 'subject') else ""
        asyncio.create_task(self.handle_envelope(envelope))

    async def _on_sys(self, envelope: dict, raw_msg):
        """系统事件回调"""
        event_type = envelope.get("type", "")
        if event_type == "online":
            aid = envelope.get("from", "")
            self.log.info(f"🟢 Agent 上线: {aid}")
        elif event_type == "offline":
            aid = envelope.get("from", "")
            self.log.info(f"🔴 Agent 下线: {aid}")

    async def _on_request(self, msg):
        """NATS request-reply 回调（从 aim.req. 过来）"""
        try:
            envelope = json.loads(msg.data)
            self.log.info(f"< Req: {envelope.get('from')}: {envelope.get('payload', {}).get('text', '')[:60]}")

            text = envelope.get("payload", {}).get("text", "")
            reply_text = await self._call_ai(text, timeout=self.AI_TIMEOUT_SHORT)

            import aim_nats_sdk as sdk
            response = sdk.make_envelope(
                from_id=self.agent_id,
                msg_type="response",
                payload={"text": reply_text or "处理失败"},
                reply_to=envelope.get("id", ""),
            )
            await msg.respond(json.dumps(response, ensure_ascii=False).encode())
        except Exception as e:
            self.log.error(f"请求处理失败: {e}")

    # ── 生命周期 ─────────────────────────

    async def start(self):
        """启动 Adapter"""
        self._running = True

        self.log.info(f"🚀 {self.agent_id} ({self.agent_name}) 启动中...")
        self.log.info(f"   框架: {self.framework}")
        self.log.info(f"   NATS: {self.nats_url}")

        try:
            # 连接 NATS（SDK 自动建立连接）
            await self.client.connect()
            await self.client.setup_streams()
            self.log.info(f"✅ 已连接到 NATS Server")

            # 通过 SDK 订阅私聊
            await self.client.subscribe_dm(self._on_dm)
            self.log.info(f"📬 已订阅私聊: aim.dm.{self.agent_id}")

            # 群聊订阅
            groups = self.DEFAULT_GROUPS
            for gid in groups:
                await self.client.subscribe_grp(gid, self._on_grp)
                self.log.info(f"📬 已订阅群聊: aim.grp.{gid}")

            # 系统事件订阅
            await self.client.subscribe_sys(self._on_sys)
            self.log.info(f"📡 已订阅系统事件")

            # 请求-回复订阅
            req_sub = await self.client.nc.subscribe(
                f"aim.req.{self.agent_id}",
                cb=self._on_request,
            )
            self.client._subscriptions[f"aim.req.{self.agent_id}"] = req_sub
            self.log.info(f"📬 已订阅请求: aim.req.{self.agent_id}")

            # 发送上线事件
            await self.client.publish_sys("online", {
                "agent_id": self.agent_id,
                "agent_name": self.agent_name,
                "framework": self.framework,
            })
            self.log.info(f"✅ {self.agent_id} 启动完成，等待消息...")

            # 保持运行
            while self._running:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            self.log.info("收到取消信号")
        except KeyboardInterrupt:
            self.log.info("收到中断信号")
        except Exception as e:
            self.log.error(f"启动异常: {e}")
            import traceback
            self.log.error(traceback.format_exc())
        finally:
            await self.shutdown()

    async def shutdown(self):
        """关闭 Adapter"""
        self._running = False
        self.log.info("🛑 关闭中...")

        # 取消活跃任务
        for msg_id, task in list(self._active_tasks.items()):
            task.cancel()
        self._active_tasks.clear()

        # 发送离线事件
        try:
            await self.client.publish_sys("offline", {
                "agent_id": self.agent_id,
                "agent_name": self.agent_name,
            })
        except Exception:
            pass

        # 断开连接
        await self.client.close()
        self.log.info("✅ 已停止")


# ── CLI ─────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AIM Agent NATS Adapter (SDK 版)")
    parser.add_argument("--agent-id", required=True, help="Agent ID (e.g., ZS0002)")
    parser.add_argument("--agent-name", default="", help="Agent Name")
    parser.add_argument("--framework", default="hermes", help="Framework (hermes/openclaw/letta)")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222", help="NATS Server URL")
    parser.add_argument("--emoji", default="🤖", help="Agent emoji")
    args = parser.parse_args()

    agent = AIMAgentNatsAdapter(
        agent_id=args.agent_id,
        agent_name=args.agent_name or args.agent_id,
        framework=args.framework,
        nats_url=args.nats_url,
        emoji=args.emoji,
    )
    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
