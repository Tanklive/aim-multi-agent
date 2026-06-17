#!/usr/bin/env python3
"""
AIM Agent 客户端 — Phase 2 持久连接模块

功能：
1. 维持长连接 WebSocket 到 AIM Server
2. 接收消息并自动回 ACK（SERVER_ACK → DELIVER_ACK → PROCESSED_ACK）
3. 发送消息时等待 ack 确认
4. 心跳检测 + 断线重连

用法:
  # 命令行（监听模式）
  python3 aim_client.py listen

  # 作为模块导入
  from aim_client import AIMClient
  client = AIMClient("ZS0002")
  await client.connect()
  await client.send("ZS0001", "你好呱呱")
  client.on_message = lambda msg: print(f"收到: {msg}")
  await client.listen()
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("❌ 需要 websockets: pip install websockets")
    sys.exit(1)

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
SECRETS_DIR = BASE_DIR / "secrets"

# ═══════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════

def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}

def get_server_url(config: dict, use_tls: bool = None) -> str:
    env_url = os.environ.get("AIM_SERVER_URL")
    if env_url:
        return env_url

    domain = config.get("domain", "")
    if use_tls is True or (use_tls is None and config.get("security", {}).get("tls", {}).get("enabled")):
        if domain:
            return f"wss://{domain}:{config.get('wss_port', 18901)}"
        host = config.get("host", "127.0.0.1")
        return f"wss://{host}:{config.get('wss_port', 18901)}"

    host = config.get("host", "127.0.0.1")
    port = config.get("ws_port", 18900)
    return f"ws://{host}:{port}"

# ═══════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════

log = logging.getLogger("aim_client")
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(_log_handler)
log.setLevel(logging.INFO)

# ═══════════════════════════════════════════════════
# Phase 2 核心模块
# ═══════════════════════════════════════════════════

class AIMClient:
    """AIM Agent 持久连接客户端 (Phase 2)

    维持一个长连接到 AIM Server，支持：
    - 自动认证（HMAC）
    - 消息接收 + 自动 ACK
    - 发送消息（等待 DELIVER_ACK）
    - 心跳 keepalive
    - 断线自动重连（指数退避）
    """

    def __init__(self, agent_id: str, config: dict = None,
                 channel: str = "main", on_message: Callable = None,
                 server_url: str = None):
        self.agent_id = agent_id
        self.config = config or load_config()
        self.channel = channel
        self.server_url = server_url or get_server_url(self.config)
        self._on_message = on_message

        # 连接状态
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self._stop = False

        # 连接参数
        self._reconnect_delay = 1.0  # 初始重连延迟
        self._max_reconnect_delay = 60.0
        self._ping_interval = 30  # 心跳间隔秒
        self._send_timeout = 10.0  # 发送超时
        self._seen_ids = set()
        self._seen_max = 1000

        # 统计
        self._sent_count = 0
        self._recv_count = 0
        self._reconnect_count = 0

    # ── 认证 ──

    def _build_auth(self) -> dict:
        """构建 HMAC 认证消息"""
        secret_file = SECRETS_DIR / f"{self.agent_id}.secret"
        if secret_file.exists():
            secret = secret_file.read_text().strip()
            ts = int(time.time())
            sig = hmac.new(
                secret.encode(),
                f"{self.agent_id}:{ts}".encode(),
                hashlib.sha256
            ).hexdigest()
            return {
                "cmd": "auth",
                "agent_id": self.agent_id,
                "timestamp": ts,
                "signature": sig,
                "channel": self.channel,
                "handler": False
            }

        # fallback: 无密钥时简单认证
        return {"cmd": "auth", "agent_id": self.agent_id, "channel": self.channel}

    # ── 连接 ──

    def _build_ssl(self):
        """构建 SSL context（自签证书跳过验证）"""
        if self.server_url.startswith("wss://"):
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    async def connect(self) -> bool:
        """建立连接到 AIM Server"""
        try:
            ssl_ctx = self._build_ssl()
            self.ws = await ws_connect(self.server_url, open_timeout=15, ssl=ssl_ctx)

            # 认证
            auth = self._build_auth()
            await self.ws.send(json.dumps(auth))
            resp = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=10))

            if resp.get("cmd") == "auth_ok" or resp.get("status") == "ok":
                self.connected = True
                self._reconnect_delay = 1.0  # 重置重连延迟
                log.info(f"✅ 已连接 AIM ({self.server_url}) [agent={self.agent_id}, channel={self.channel}]")
                return True
            else:
                log.error(f"❌ 认证失败: {resp}")
                await self.ws.close()
                self.ws = None
                return False

        except Exception as e:
            log.error(f"❌ 连接失败: {e}")
            self.ws = None
            return False

    async def disconnect(self):
        """断开连接"""
        self._stop = True
        self.connected = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        log.info("已断开连接")

    # ── 发送（Phase 2：带 ACK 确认） ──

    async def send(self, to_id: str, content: str,
                   timeout: float = None, max_retries: int = 3) -> dict:
        """发送消息并等待 Server ACK

        使用独立短连接发送（不干扰监听线程的 recv）。
        支持指数退避重传。

        返回:
            {"success": True, "msg_id": str, "delivered": bool, "detail": str}
        """
        msg_id = str(uuid.uuid4())[:12]
        timeout = timeout or self._send_timeout
        retry_count = 0

        while retry_count <= max_retries:
            try:
                ssl_ctx = self._build_ssl()
                async with ws_connect(self.server_url, open_timeout=10, ssl=ssl_ctx) as ws:
                    # 认证
                    await ws.send(json.dumps(self._build_auth()))
                    try:
                        auth_resp = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        retry_count += 1
                        log.warning(f"⏱ 认证超时 [retry={retry_count}/{max_retries}]")
                        await asyncio.sleep(2)
                        continue

                    # 发送消息
                    send_cmd = {
                        "cmd": "send",
                        "to": to_id,
                        "content": content,
                        "msg_id": msg_id,
                        "retry_count": retry_count,
                    }
                    await ws.send(json.dumps(send_cmd))
                    self._sent_count += 1

                    # 等待 Server ACK
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    ack_data = json.loads(raw)
                    if ack_data.get("cmd") == "ack":
                        delivered = ack_data.get("delivered", True)
                        return {
                            "success": True,
                            "msg_id": msg_id,
                            "delivered": delivered,
                            "detail": f"✅ 消息已{'送达' if delivered else '发送（未ACK）'} (to={to_id})"
                        }

            except (asyncio.TimeoutError, OSError, ConnectionRefusedError,
                    websockets.WebSocketException) as e:
                retry_count += 1
                if retry_count <= max_retries:
                    delay = min(5.0 * (2 ** (retry_count - 1)), 60.0)
                    jitter = delay * 0.2 * (random.random() * 2 - 1)
                    log.warning(f"⏱ 发送异常 [{type(e).__name__}]，重试 [retry={retry_count}/{max_retries}, delay={delay:.1f}s]")
                    await asyncio.sleep(delay + jitter)
                continue
            except Exception as e:
                retry_count += 1
                if retry_count <= max_retries:
                    log.warning(f"⚠️ 发送异常: {e}，重试 [retry={retry_count}/{max_retries}]")
                    await asyncio.sleep(3)
                continue

        return {
            "success": False,
            "msg_id": msg_id,
            "delivered": False,
            "detail": f"❌ 发送失败（{max_retries+1}次重试均无ACK）"
        }

    # ── 接收 + ACK ──

    def _is_duplicate(self, msg_id: str) -> bool:
        """接收端去重"""
        if msg_id in self._seen_ids:
            return True
        self._seen_ids.add(msg_id)
        # LRU 限制
        if len(self._seen_ids) > self._seen_max:
            self._seen_ids.pop()
        return False

    async def _send_ack(self, msg_id: str, delivered: bool = True):
        """发送 ACK 给 Server"""
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send(json.dumps({
                    "cmd": "ack",
                    "msg_id": msg_id,
                    "delivered": delivered,
                }))
            except Exception:
                pass

    async def _handle_message(self, payload: dict):
        """处理收到的消息"""
        msg = payload.get("msg", payload)

        # 去重
        msg_id = msg.get("msg_id", "")
        if msg_id and self._is_duplicate(msg_id):
            return

        # 收到消息 → SERVER_ACK（由 Server 自己处理）
        # 我们只回 DELIVER_ACK（确认已收到）
        if msg_id:
            await self._send_ack(msg_id, delivered=True)

        self._recv_count += 1

        # P2: 如果有任务状态跟踪，发 PROCESSED_ACK
        # （处理完成由 Agent 框架通知，此处仅标记已送达）

        # 调用用户回调
        if self._on_message:
            try:
                if asyncio.iscoroutinefunction(self._on_message):
                    await self._on_message(msg)
                else:
                    self._on_message(msg)
            except Exception as e:
                log.error(f"消息回调异常: {e}")

    async def _handle_ack(self, payload: dict):
        """处理 Server/Agent 的 ACK（静默接收，短连接模式已在 send() 中处理）"""
        pass

    # ── 监听循环 ──

    async def _listen_loop(self):
        """持续监听消息"""
        while not self._stop:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=self._ping_interval)
                data = json.loads(raw)
                cmd = data.get("cmd", "")

                if cmd == "message":
                    asyncio.create_task(self._handle_message(data))
                elif cmd == "ack":
                    await self._handle_ack(data)
                elif cmd == "pong":
                    pass  # 心跳回复
                elif cmd == "ping":
                    try:
                        await self.ws.send(json.dumps({"cmd": "pong"}))
                    except Exception:
                        pass
                elif cmd == "auth_ok":
                    pass  # 已经在 connect() 里处理
                elif cmd == "error":
                    log.warning(f"⚠️ Server 错误: {data}")
                else:
                    log.debug(f"未知命令: {cmd}")

            except asyncio.TimeoutError:
                # 心跳保活：发送 ping
                if self.ws and not self.ws.closed:
                    try:
                        await self.ws.send(json.dumps({"cmd": "ping"}))
                    except Exception:
                        log.warning("心跳发送失败，准备重连")
                        break
            except (websockets.ConnectionClosed, OSError, Exception) as e:
                log.warning(f"⚠️ 连接中断: {e}")
                break

        self.connected = False

    # ── 主入口 ──

    async def run(self):
        """启动客户端（自动连接+监听+断线重连）"""
        log.info(f"🚀 AIM 客户端启动 [agent={self.agent_id}, channel={self.channel}]")

        while not self._stop:
            if not self.connected or not self.ws:
                ok = await self.connect()
                if not ok:
                    # 指数退避重连
                    log.info(f"⏳ 等待 {self._reconnect_delay:.0f}s 后重连...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
                    self._reconnect_count += 1
                    continue

            await self._listen_loop()

        log.info("🛑 AIM 客户端已停止")

    def stop(self):
        """停止客户端"""
        self._stop = True
        log.info("正在停止...")


# ═══════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════

async def cmd_listen():
    """监听模式"""
    config = load_config()
    agent_id = os.environ.get("AIM_AGENT_ID", "ZS0002")
    channel = os.environ.get("AIM_CHANNEL", "main")

    # 设置消息回调
    def on_message(msg):
        sender = msg.get("from_id") or msg.get("from", "")
        content = msg.get("content", "")
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] 📩 {sender}: {content[:200]}")

    client = AIMClient(agent_id, config=config, channel=channel, on_message=on_message)

    # 优雅关闭
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, client.stop)
        except NotImplementedError:
            pass  # Windows 不支持 add_signal_handler

    await client.run()


def main():
    if len(sys.argv) < 2:
        print("AIM 客户端 — Phase 2 持久连接模块")
        print()
        print("用法:")
        print(f"  python3 {sys.argv[0]} listen          listen      监听消息")
        print(f"  python3 {sys.argv[0]} send <to> <msg>   发送消息")
        print()
        print("环境变量:")
        print("  AIM_AGENT_ID=ZS0002    Agent ID（默认 ZS0002）")
        print("  AIM_CHANNEL=main       通道名（默认 main）")
        print("  AIM_SERVER_URL=...      覆盖 Server 地址")
        print()
        print("示例:")
        print(f"  python3 {sys.argv[0]} listen")
        print(f"  AIM_AGENT_ID=ZS0002 python3 {sys.argv[0]} listen")
        print(f"  python3 {sys.argv[0]} send ZS0001 '你好呱呱'")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "listen":
        asyncio.run(cmd_listen())
    elif cmd == "send" and len(sys.argv) >= 4:
        to_id = sys.argv[2]
        content = sys.argv[3]
        config = load_config()
        agent_id = os.environ.get("AIM_AGENT_ID", "ZS0002")

        async def do_send():
            client = AIMClient(agent_id, config=config)
            await client.connect()
            result = await client.send(to_id, content)
            print(result["detail"])
            await client.disconnect()

        asyncio.run(do_send())
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
