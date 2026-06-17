#!/usr/bin/env python3
"""
AIM Status Feedback — 状态回推 + Observer 监听
===============================================
客户端模块：
1. send_status_feedback() — Agent 处理任务时回推状态给 Server
2. AIMObserver — 连接 Server 监听某 Agent 的状态流
3. aim watch CLI — 实时展示状态

协议（aim-status-v1）：
  发送端 → Server: {cmd: "status_feedback", session_id, step, status, progress, duration_ms}
  Server → Observer: {msg_type: "status_feedback", from, session_id, step, status, progress, seq, ...}
  Server → 发送端: {cmd: "status_feedback_ack", dropped: bool}
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("❌ 需要 websockets: pip install websockets")
    sys.exit(1)

# ═══ 配置 ═══
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
SECRETS_DIR = BASE_DIR / "secrets"

log = logging.getLogger("aim_status")
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(_handler)
log.setLevel(logging.INFO)


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def _get_server_url(config: dict) -> str:
    env_url = os.environ.get("AIM_SERVER_URL")
    if env_url:
        return env_url
    host = config.get("host", "127.0.0.1")
    port = config.get("ws_port", 18900)
    return f"ws://{host}:{port}"


def _build_auth(agent_id: str, channel: str = "main", **extra) -> dict:
    """构建认证消息"""
    secret_file = SECRETS_DIR / f"{agent_id}.secret"
    import hmac
    import hashlib
    if secret_file.exists():
        secret = secret_file.read_text().strip()
        ts = int(time.time())
        sig = hmac.new(
            secret.encode(),
            f"{agent_id}:{ts}".encode(),
            hashlib.sha256
        ).hexdigest()
        auth = {
            "cmd": "auth",
            "agent_id": agent_id,
            "timestamp": ts,
            "signature": sig,
            "channel": channel,
        }
    else:
        auth = {"cmd": "auth", "agent_id": agent_id, "channel": channel}
    auth.update(extra)
    return auth


def _build_ssl(server_url: str):
    if server_url.startswith("wss://"):
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


# ═══════════════════════════════════════════════════
# 1. Status Feedback 发送端
# ═══════════════════════════════════════════════════

class StatusFeedbackSender:
    """状态回推发送器 — Agent 处理任务时调用"""

    def __init__(self, agent_id: str, config: dict = None):
        self.agent_id = agent_id
        self.config = config or _load_config()
        self.server_url = _get_server_url(self.config)
        self._ws = None
        self._connected = False

    async def connect(self) -> bool:
        """建立连接到 Server"""
        try:
            ssl_ctx = _build_ssl(self.server_url)
            self._ws = await ws_connect(self.server_url, open_timeout=10, ssl=ssl_ctx)
            auth = _build_auth(self.agent_id, channel="main")
            await self._ws.send(json.dumps(auth))
            resp = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))
            if resp.get("cmd") == "auth_ok" or resp.get("status") == "ok":
                self._connected = True
                return True
            log.error(f"❌ 认证失败: {resp}")
            return False
        except Exception as e:
            log.error(f"❌ 连接失败: {e}")
            return False

    async def disconnect(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._connected = False

    async def send(self, session_id: str, step: str, status: str = "running",
                   progress: str = "", duration_ms: int = 0) -> dict:
        """发送 status_feedback
        
        Args:
            session_id: 会话 ID（关联原始消息的 msg_id）
            step: 当前步骤名
            status: running | completed | failed | timeout
            progress: 进度描述
            duration_ms: 耗时毫秒
        
        Returns:
            {"success": bool, "dropped": bool, "reason": str}
        """
        if not self._connected or not self._ws:
            ok = await self.connect()
            if not ok:
                return {"success": False, "dropped": False, "reason": "连接失败"}

        payload = {
            "cmd": "status_feedback",
            "session_id": session_id,
            "step": step,
            "status": status,
            "progress": progress,
            "duration_ms": duration_ms,
        }

        try:
            await self._ws.send(json.dumps(payload))
            raw = await asyncio.wait_for(self._ws.recv(), timeout=5)
            ack = json.loads(raw)
            return {
                "success": True,
                "dropped": ack.get("dropped", False),
                "reason": ack.get("reason", ""),
            }
        except Exception as e:
            log.warning(f"⚠️ status_feedback 发送失败: {e}")
            self._connected = False
            return {"success": False, "dropped": False, "reason": str(e)}


async def send_status_feedback(agent_id: str, session_id: str, step: str,
                                status: str = "running", progress: str = "",
                                duration_ms: int = 0, config: dict = None) -> dict:
    """便捷函数：发送单条 status_feedback（自动连接+断开）"""
    sender = StatusFeedbackSender(agent_id, config)
    ok = await sender.connect()
    if not ok:
        return {"success": False, "dropped": False, "reason": "连接失败"}
    try:
        return await sender.send(session_id, step, status, progress, duration_ms)
    finally:
        await sender.disconnect()


# ═══════════════════════════════════════════════════
# 2. Observer 端 — 监听 Agent 状态流
# ═══════════════════════════════════════════════════

class AIMObserver:
    """AIM Observer — 监听指定 Agent 的 status_feedback 流"""

    def __init__(self, agent_id: str, watch_target: str, config: dict = None,
                 on_status: Callable = None, verbose: bool = False):
        """
        Args:
            agent_id: 自己的 Agent ID（observer 身份）
            watch_target: 要监听的目标 Agent ID
            config: AIM 配置
            on_status: 回调函数 fn(status_data)
            verbose: 是否显示详细信息
        """
        self.agent_id = agent_id
        self.watch_target = watch_target
        self.config = config or _load_config()
        self.server_url = _get_server_url(self.config)
        self.on_status = on_status
        self.verbose = verbose
        self._ws = None
        self._stop = False
        self._last_seq = 0
        self._msg_count = 0

    async def connect(self) -> bool:
        """连接 Server 并注册为 observer"""
        try:
            ssl_ctx = _build_ssl(self.server_url)
            self._ws = await ws_connect(self.server_url, open_timeout=10, ssl=ssl_ctx)
            auth = _build_auth(
                self.agent_id,
                channel="observer",
                watch_target=self.watch_target,
                last_seq=self._last_seq,
                verbose=self.verbose,
            )
            await self._ws.send(json.dumps(auth))
            resp = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))

            if resp.get("cmd") == "auth_ok" and resp.get("observer"):
                log.info(f"👀 已连接: watching {self.watch_target}")
                return True
            log.error(f"❌ 认证失败: {resp}")
            return False
        except Exception as e:
            log.error(f"❌ 连接失败: {e}")
            return False

    async def listen(self):
        """持续监听状态流"""
        while not self._stop:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                data = json.loads(raw)

                if data.get("msg_type") == "status_feedback":
                    self._msg_count += 1
                    self._last_seq = data.get("seq", self._last_seq)

                    if self.on_status:
                        if asyncio.iscoroutinefunction(self.on_status):
                            await self.on_status(data)
                        else:
                            self.on_status(data)
                    else:
                        self._print_status(data)

            except asyncio.TimeoutError:
                # 心跳保活
                if self._ws and not self._ws.closed:
                    try:
                        await self._ws.send(json.dumps({"cmd": "ping"}))
                    except Exception:
                        break
            except Exception as e:
                log.warning(f"⚠️ 连接中断: {e}")
                break

    def _print_status(self, data: dict):
        """默认打印格式"""
        ts = datetime.fromtimestamp(data.get("timestamp", 0)).strftime("%H:%M:%S")
        sender = data.get("from", "?")
        step = data.get("step", "")
        status = data.get("status", "")
        progress = data.get("progress", "")
        dur = data.get("duration_ms", 0)
        seq = data.get("seq", 0)

        # 状态图标
        icons = {"running": "🔄", "completed": "✅", "failed": "❌", "timeout": "⏰"}
        icon = icons.get(status, "❓")

        line = f"[{ts}] {icon} {sender} | {step}"
        if progress:
            line += f" | {progress}"
        if dur > 0:
            line += f" ({dur}ms)"
        print(line)

    def stop(self):
        self._stop = True

    async def run(self):
        """主循环：连接 + 监听 + 断线重连"""
        delay = 1.0
        while not self._stop:
            ok = await self.connect()
            if not ok:
                log.info(f"⏳ {delay:.0f}s 后重连...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            delay = 1.0
            await self.listen()
        log.info("🛑 Observer 已停止")


# ═══════════════════════════════════════════════════
# 3. CLI — aim watch
# ═══════════════════════════════════════════════════

def _format_watch_line(data: dict) -> str:
    """格式化 watch 输出行"""
    ts = datetime.fromtimestamp(data.get("timestamp", 0)).strftime("%H:%M:%S")
    sender = data.get("from", "?")
    step = data.get("step", "")
    status = data.get("status", "")
    progress = data.get("progress", "")
    dur = data.get("duration_ms", 0)
    session_id = data.get("session_id", "")[:12]

    icons = {"running": "🔄", "completed": "✅", "failed": "❌", "timeout": "⏰"}
    icon = icons.get(status, "❓")

    parts = [f"[{ts}]", icon, sender]
    if step:
        parts.append(f"| {step}")
    if progress:
        parts.append(f"| {progress}")
    if session_id:
        parts.append(f"| sid={session_id}")
    if dur > 0:
        parts.append(f"| {dur}ms")
    return " ".join(parts)


async def cmd_watch():
    """aim watch CLI 入口"""
    agent_id = os.environ.get("AIM_AGENT_ID", "ZS0001")
    watch_target = sys.argv[2] if len(sys.argv) > 2 else "ZS0002"

    print(f"👀 AIM Watch — 监听 {watch_target} 的状态流")
    print(f"   自己: {agent_id} | Ctrl+C 退出")
    print("─" * 60)

    def on_status(data):
        print(_format_watch_line(data))

    observer = AIMObserver(agent_id, watch_target, on_status=on_status)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, observer.stop)
        except NotImplementedError:
            pass

    await observer.run()


async def cmd_send_test():
    """测试发送 status_feedback"""
    agent_id = os.environ.get("AIM_AGENT_ID", "ZS0001")
    session_id = sys.argv[2] if len(sys.argv) > 2 else str(uuid.uuid4())[:12]
    step = sys.argv[3] if len(sys.argv) > 3 else "test_step"
    status = sys.argv[4] if len(sys.argv) > 4 else "running"
    progress = sys.argv[5] if len(sys.argv) > 5 else "测试状态回推"

    result = await send_status_feedback(agent_id, session_id, step, status, progress)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) < 2:
        print("AIM Status Feedback — 状态回推 + Observer 监听")
        print()
        print("用法:")
        print(f"  python3 {sys.argv[0]} watch <target_id>     监听目标 Agent 状态")
        print(f"  python3 {sys.argv[0]} send [session_id] [step] [status] [progress]")
        print()
        print("示例:")
        print(f"  python3 {sys.argv[0]} watch ZS0002")
        print(f"  python3 {sys.argv[0]} send abc123 process running '处理中'")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "watch":
        asyncio.run(cmd_watch())
    elif cmd == "send":
        asyncio.run(cmd_send_test())
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
