#!/usr/bin/env python3
"""AIM Agent NATS — 吉量 ZS0002 NATS 原生接入端 (Veritas 协议)

使用 aim_nats_sdk.py（标准 Veritas SDK）替代 WebSocket Hub。
集成 FrameworkCLI AI 调用 + Pin 去重 + Observer 事件 + 消息归档。

启动:
    python3 aim_agent_nats.py --agent-id ZS0002 --framework hermes

架构:
    AIMAgentNATS
    ├── AIMNATSClient (Veritas SDK — 连接/订阅/发送)
    ├── FrameworkCLI (AI 调用)
    ├── MessageDedup (内存 LRU 去重)
    ├── MessageArchive (JSONL 归档)
    └── Observer 事件 / 心跳

Author: 吉量 🐴
Protocol: AIM Veritas (§4, aim-veritas.md)
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Dict, Optional, Any

# ── 路径 ──────────────────────────────────

BASE_DIR = Path(__file__).parent
AIM_BASE = Path.home() / ".hermes" / "aim"
LOG_DIR = AIM_BASE / "logs"
DATA_DIR = AIM_BASE / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 引入标准 SDK
SDK_PATH = Path.home() / ".aim" / "bin" / "aim_nats_sdk.py"
if SDK_PATH.exists():
    sys.path.insert(0, str(SDK_PATH.parent))

try:
    from aim_nats_sdk import AIMNATSClient, Subjects, make_envelope, parse_message
except ImportError:
    print("ERROR: 未找到 aim_nats_sdk.py，请确认 ~/.aim/bin/ 下存在")
    print(f"  搜索路径: {SDK_PATH}")
    sys.exit(1)

# AIM Pin 持久化去重
PIN_PATH = Path.home() / "shared" / "aim" / "aim_pin.py"
if PIN_PATH.exists():
    sys.path.insert(0, str(PIN_PATH.parent))
try:
    from aim_pin import AIMPin
except ImportError:
    AIMPin = None  # fallback: 没有 PIN 就保持内存去重

# AIM Agent 共享模块（AIM_BASE 或当前目录）
sys.path.insert(0, str(AIM_BASE))
sys.path.insert(0, str(BASE_DIR))
from framework_cli import FrameworkCLI
from ai_types import AIRequest, AIResponse

# ── 日志 ──────────────────────────────────


def setup_logging(agent_id: str) -> logging.Logger:
    """配置日志（文件 + 控制台）"""
    log_file = LOG_DIR / f"nats-agent-{agent_id}.log"
    log = logging.getLogger(f"aim-nats-{agent_id}")
    log.setLevel(logging.DEBUG)

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
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    ))
    log.addHandler(ch)

    return log


# ── 消息归档 ─────────────────────────────


class MessageArchive:
    """消息归档到 JSONL"""

    def __init__(self, agent_id: str):
        self.file = DATA_DIR / f"nats_messages_{agent_id}.jsonl"

    def archive(self, msg: dict):
        try:
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            log.warning(f"归档失败: {e}")


# ── 组件 ──────────────────────────────────


class AIMAgentNATS:
    """AIM Agent NATS — 吉量 🐴

    使用 Veritas SDK（aim_nats_sdk.py）连接到 NATS Server，
    监听 aim.dm.* / aim.grp.* 消息，调用 AI 处理后回复。
    """

    MAX_CONCURRENT = 3
    AI_TIMEOUT_DEFAULT = 120
    AI_TIMEOUT_SHORT = 180
    AI_TIMEOUT_LONG = 300
    HEARTBEAT_INTERVAL = 300
    PING_INTERVAL = 20

    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        framework: str = "hermes",
        nats_url: str = "nats://127.0.0.1:4222",
        emoji: str = "🐴",
    ):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.framework = framework
        self.nats_url = nats_url
        self.emoji = emoji

        self.log = setup_logging(agent_id)
        self.log.info(f"🚀 {emoji} AIM Agent NATS v1.0 初始化")

        # SDK 客户端（从 config/aim.json 自动读取 token，命令行 URL 覆盖配置）
        self.client = AIMNATSClient.from_config(agent_id, server=nats_url)

        # 去重 + 归档
        if AIMPin is not None:
            self.dedup = AIMPin(agent_id, ttl=300, max_memory=2000)
        else:
            self.dedup = None
        self.archive = MessageArchive(agent_id)

        # 并发控制
        self.semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._active_tasks: Dict[str, asyncio.Task] = {}

        # AI 调用
        self._cli_paths = self._load_cli_paths()
        self._commands = self._load_commands_config()
        self._fw_cli = FrameworkCLI(self.framework, self._commands, self._cli_paths)

        # 运行状态
        self._running = False
        self._last_reply_time = 0
        self._reply_cooldown = 3

        # 消息回调注册
        self._dm_handlers: list = []
        self._group_handlers: list = []

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
        cli_paths = {}
        agents = config.get("agents", {})
        for aid, info in agents.items():
            if isinstance(info, dict) and "cli" in info:
                cli_paths[aid] = os.path.expanduser(info["cli"])
        defaults = {
            "hermes": os.path.expanduser("~/.local/bin/hermes"),
            "openclaw": os.path.expanduser("~/.npm-global/bin/openclaw"),
            "letta": "letta",
        }
        for aid, cli in defaults.items():
            if aid not in cli_paths:
                cli_paths[aid] = cli
        return cli_paths

    def _load_commands_config(self) -> dict:
        config = self._load_config()
        return config.get("commands", {})

    # ── AI 调用 ─────────────────────────

    async def _call_ai(self, prompt: str, timeout: Optional[int] = None) -> str:
        """调用 AI 处理消息"""
        if timeout is None:
            timeout = self.AI_TIMEOUT_DEFAULT

        if self._fw_cli:
            try:
                request = AIRequest(
                    prompt=prompt,
                    agent_id=self.agent_id,
                    timeout=timeout,
                )
                response: AIResponse = await self._fw_cli.call(request)
                if response.success and response.text:
                    return response.text.strip()
                self.log.warning(f"AI 调用未返回内容: {response.error}")
            except Exception as e:
                self.log.error(f"FrameworkCLI 调用失败: {e}")

        return await self._fallback_call_ai(prompt, timeout)

    async def _fallback_call_ai(self, prompt: str, timeout: int) -> str:
        """降级：直接执行 hermes chat -q"""
        cli_path = self._cli_paths.get(self.framework, self.framework)
        cmd = [cli_path, "chat", "-q", prompt, "-Q"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            if proc.returncode == 0:
                output = stdout.decode("utf-8", errors="replace").strip()
                lines = [line for line in output.split("\n")
                         if not line.startswith("⚠️")]
                return "\n".join(lines).strip()
            else:
                self.log.error(f"CLI 调用失败 (rc={proc.returncode}): {stderr.decode()[:200]}")
        except asyncio.TimeoutError:
            self.log.error(f"AI 调用超时 ({timeout}s)")
        except FileNotFoundError:
            self.log.error(f"CLI 未找到: {cli_path}")
        except Exception as e:
            self.log.error(f"AI 调用异常: {e}")

        return ""

    # ── 消息处理 ─────────────────────────

    async def handle_message(self, msg_data: dict):
        """处理一条消息（私聊或群聊）"""
        # 从标准信封解析
        msg_id = msg_data.get("id", "")
        if not msg_id:
            # 可能已有 msg_id 字段
            msg_id = msg_data.get("msg_id", "")
        if not msg_id:
            return

        # 去重（AIMPin 持久化版）
        if self.dedup is not None:
            if await self.dedup.is_duplicate(msg_id):
                self.log.debug(f"🔄 跳过去重消息: {msg_id}")
                return
            await self.dedup.mark(msg_id)

        msg_type = msg_data.get("type", "dm")
        from_id = msg_data.get("from", "")
        payload = msg_data.get("payload", {})
        content = ""
        if isinstance(payload, dict):
            content = payload.get("text", "")
        else:
            content = str(payload)

        if not content and msg_data.get("content"):
            content = msg_data["content"]

        if not content:
            return

        # 不自言自语
        if from_id == self.agent_id:
            self.log.debug(f"🚫 跳过自己的消息: {msg_id}")
            return

        # Observer: 已收到（去重通过）
        await self.client.emit_obs("received", msg_id, f"收到来自 {from_id} 的{msg_type}消息")

        # 归档
        self.archive.archive(msg_data)

        # 并发处理
        task = asyncio.create_task(
            self._process_message(msg_id, msg_type, from_id, content, msg_data)
        )
        self._active_tasks[msg_id] = task
        task.add_done_callback(lambda t: self._active_tasks.pop(msg_id, None))

    async def _process_message(
        self, msg_id: str, msg_type: str, from_id: str,
        content: str, raw_msg: dict
    ):
        """处理消息（AI 调用 + 回复）"""
        group_id = raw_msg.get("meta", {}).get("group", "") if isinstance(raw_msg.get("meta"), dict) else ""

        log.info(f"⚙️ [{msg_id[:8]}] {msg_type} from={from_id}: {content[:80]}")

        # Observer: 处理中 — 在 semaphore 外发出，避免被并发槽占满阻塞
        await self.client.emit_obs("processing", msg_id, f"处理 {from_id} 的消息")

        async with self.semaphore:
            try:
                # 构造 prompt
                prompt = f"[{from_id}]: {content}" if msg_type == "dm" else f"[{from_id} 群聊]: {content}"

                # Observer: AI 调用开始
                await self.client.emit_obs("ai_start", msg_id, f"调用 AI 框架处理: {content[:50]}...")
                reply = await self._call_ai(prompt)

                if reply:
                    # Observer: AI 回复完成
                    await self.client.emit_obs("ai_done", msg_id, f"AI 回复: {reply[:80]}")

                    # 发送回复
                    if msg_type == "dm":
                        await self.client.send_dm(from_id, reply, reply_to=msg_id)
                        log.info(f"✅ 回复 {from_id}: {reply[:60]}...")
                    elif msg_type == "grp" and group_id:
                        await self.client.send_grp(group_id, reply)
                        log.info(f"✅ 回复群聊 {group_id}: {reply[:60]}...")
                    elif msg_type == "grp" and raw_msg.get("group"):
                        await self.client.send_grp(raw_msg["group"], reply)
                        log.info(f"✅ 回复群聊 {raw_msg['group']}: {reply[:60]}...")

                    # Observer: 完成
                    await self.client.emit_obs("completed", msg_id, "已回复")
                else:
                    # Observer: AI 无回复
                    await self.client.emit_obs("ai_empty", msg_id, "AI 未生成回复")
                    log.warning(f"⚠️ AI 未生成回复，跳过: {msg_id}")

            except Exception as e:
                log.error(f"❌ 处理消息失败 [{msg_id[:8]}]: {e}")
                import traceback
                log.error(traceback.format_exc())
                await self.client.emit_obs("error", msg_id, str(e))

    # ── NATS 回调 ────────────────────────

    async def _on_dm_msg(self, envelope: dict, raw_msg):
        """私聊消息回调 (aim.dm.<agent_id>)"""
        try:
            log.debug(f"< 私聊: {envelope.get('from')} → {str(envelope.get('payload', {}).get('text', ''))[:80]}")
            await self.handle_message(envelope)
        except Exception as e:
            log.error(f"私聊处理失败: {e}")

    async def _on_grp_msg(self, envelope: dict, raw_msg):
        """群聊消息回调 (aim.grp.*)"""
        try:
            from_id = envelope.get("from", "")
            content = envelope.get("payload", {}).get("text", "")
            log.debug(f"< 群聊: {from_id} → {content[:80]}")
            await self.handle_message(envelope)
        except Exception as e:
            log.error(f"群聊处理失败: {e}")

    async def _on_request(self, raw_msg):
        """请求-回复回调 (aim.req.<agent_id>)"""
        try:
            data = parse_message(raw_msg.data)
            log.info(f"< 请求: {data.get('from')}: {str(data.get('payload', {}).get('text', ''))[:80]}")

            content = data.get("payload", {}).get("text", "")
            reply = await self._call_ai(content, timeout=self.AI_TIMEOUT_SHORT)

            response = make_envelope(
                from_id=self.agent_id,
                msg_type="response",
                payload={"text": reply or "处理失败"},
            )
            await raw_msg.respond(json.dumps(response, ensure_ascii=False).encode())
        except Exception as e:
            log.error(f"请求处理失败: {e}")

    # ── 生命周期 ─────────────────────────

    async def on_connect(self):
        """连接成功后回调"""
        log.info(f"🟢 [{self.agent_id}] 已连接到 NATS: {self.nats_url}")
        await self.client.emit_obs("agent_online", "", f"{self.agent_id} ({self.agent_name}) 已上线")

    async def on_disconnect(self):
        """断开连接后回调"""
        log.info(f"🔴 [{self.agent_id}] 已断开 NATS 连接")
        await self.client.emit_obs("agent_offline", "", f"{self.agent_id} ({self.agent_name}) 已下线")

    async def heartbeat_task(self):
        """定期心跳"""
        while self._running:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            if self.client.is_connected:
                await self.client.emit_obs("heartbeat", "", f"{self.agent_id} alive")
            else:
                log.warning("⏳ NATS 未连接，跳过心跳")

    # ── 注册 ─────────────────────────────

    async def register(self) -> bool:
        """通过 aim.reg.register request-reply 注册/确认身份"""
        log.info(f"📝 [{self.agent_id}] 注册中 (aim.reg.register)...")
        try:
            request = {
                "agent_id": self.agent_id,
                "agent_name": self.agent_name,
                "framework": self.framework,
                "emoji": self.emoji,
            }
            response = await self.client.nc.request(
                "aim.reg.register",
                json.dumps(request).encode(),
                timeout=5,
            )
            result = json.loads(response.data)
            log.info(f"✅ 注册成功: {result}")
            return True
        except asyncio.TimeoutError:
            log.warning("⚠️ 注册超时，Server 可能未运行注册服务，跳过")
            return True
        except Exception as e:
            log.warning(f"⚠️ 注册失败 ({e})，降级跳过")
            return True

    # ── 启动停止 ─────────────────────────

    async def start(self):
        """启动 Agent（含自动重连）"""
        self._running = True

        log.info(f"🚀 {self.emoji} {self.agent_id} ({self.agent_name}) NATS Agent 启动")
        log.info(f"   NATS: {self.nats_url}")
        log.info(f"   框架: {self.framework}")

        retry_delay = 2
        while self._running:
            try:
                # 连接 NATS
                await self.client.connect()
                await self.client.setup_streams()
                await self.on_connect()

                # 注册
                await self.register()

                # 订阅私聊 (SDK subscribe_dm)
                await self.client.subscribe_dm(self._on_dm_msg)
                log.info("📬 已订阅私聊: aim.dm.ZS0002")

                # 订阅请求
                sub = await self.client.nc.subscribe(
                    f"aim.req.{self.agent_id}", cb=self._on_request
                )
                log.info("📬 已订阅请求: aim.req.ZS0002")

                # 订阅群聊
                for gid in ["grp_trio"]:
                    await self.client.subscribe_grp(gid, self._on_grp_msg)
                    log.info(f"📬 已订阅群聊: aim.grp.{gid}")

                # 心跳
                hb_task = asyncio.create_task(self.heartbeat_task())

                retry_delay = 2  # 重置重试延迟
                log.info(f"✅ {self.agent_id} 启动完成，等待消息...")

                # 保持运行
                while self._running:
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                log.info("收到取消信号，停止")
                break
            except KeyboardInterrupt:
                log.info("收到中断信号，停止")
                break
            except Exception as e:
                log.error(f"❌ 运行异常: {e}")
                import traceback
                log.error(traceback.format_exc())
                if not self._running:
                    break
                # 自动重连
                log.info(f"⏳ {retry_delay}s 后重连...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
            finally:
                await self.shutdown()

    async def shutdown(self):
        """关闭 Agent"""
        self._running = False
        log.info("🛑 关闭中...")

        for msg_id, task in list(self._active_tasks.items()):
            task.cancel()
        self._active_tasks.clear()

        await self.on_disconnect()
        await self.client.close()
        log.info("✅ 已停止")


# ── CLI ──────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AIM Agent NATS (Veritas)")
    parser.add_argument("--agent-id", default="ZS0002", help="Agent ID")
    parser.add_argument("--agent-name", default="吉量", help="Agent 名称")
    parser.add_argument("--framework", default="hermes", help="AI 框架")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222", help="NATS Server URL")
    parser.add_argument("--emoji", default="🐴", help="Agent emoji")
    args = parser.parse_args()

    global log
    log = setup_logging(args.agent_id)

    agent = AIMAgentNATS(
        agent_id=args.agent_id,
        agent_name=args.agent_name,
        framework=args.framework,
        nats_url=args.nats_url,
        emoji=args.emoji,
    )

    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        log.info("收到 SIGINT")


if __name__ == "__main__":
    main()
