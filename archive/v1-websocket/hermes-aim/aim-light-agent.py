#!/usr/bin/env python3
"""
AIM Agent 轻量客户端 — 消息接收守护

不依赖任何特定 AI 框架。小火鸡儿只需要实现一个 CLI 回调脚本，
每次收到消息时系统会调用这个脚本进行处理。

安装:
  pip install websockets
  
用法:
  python3 aim-agent.py --agent-id ZS0003 --callback /path/to/handler.sh

支持注册制（自动获取 ID）和种子 Agent（已有 ID+密钥）两种模式。
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("ERROR: pip install websockets")
    sys.exit(1)

# ── 配置 ──────────────────────────────────────────────────

DEFAULT_SERVER = "ws://127.0.0.1:18900"
DATA_DIR = Path.home() / ".aim" / "data"
LOG_DIR = Path.home() / ".aim" / "logs"
SECRET_DIR = Path.home() / ".aim" / "secrets"
AGENTS_DIR = Path.home() / ".aim" / "agents"  # Agent 独立目录

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
SECRET_DIR.mkdir(parents=True, exist_ok=True)
AGENTS_DIR.mkdir(parents=True, exist_ok=True)

MSG_FILE = DATA_DIR / "messages.jsonl"
STATUS_LOG = DATA_DIR / "status_log.jsonl"

# ── 日志 ──────────────────────────────────────────────────

log = logging.getLogger("aim-agent")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                       datefmt="%H:%M:%S"))
log.addHandler(handler)
log.setLevel(logging.INFO)


# ── 辅助函数 ──────────────────────────────────────────────

def build_auth_payload(agent_id: str, secret: str, channel: str = "main") -> dict:
    """构建 HMAC 签名认证载荷"""
    ts = int(time.time())
    message = f"{agent_id}:{ts}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        "cmd": "auth",
        "agent_id": agent_id,
        "channel": channel,
        "handler": True,
        "term": 1,
        "timestamp": ts,
        "signature": sig,
    }


def build_register_payload(agent_name: str, framework: str, operator_name: str = "") -> dict:
    """构建注册请求载荷"""
    return {
        "cmd": "register",
        "operator": {"name": operator_name or agent_name, "contact": ""},
        "agent_name": agent_name,
        "framework": framework or "custom",
        "capabilities": ["chat", "task", "status_feedback"],
    }


def load_file(path: Path) -> str:
    if path.exists():
        return path.read_text().strip()
    return ""


def find_secret(agent_id: str, custom_secret: str = "") -> str:
    """查找 secret 文件（支持多目录）
    
    查找顺序：
    1. 用户指定的 secret
    2. ~/.aim/agents/agent-XX/secrets/{agent_id}.secret
    3. ~/.aim/secrets/{agent_id}.secret
    4. 环境变量 AIM_SECRET
    """
    # 1. 用户指定
    if custom_secret:
        return custom_secret
    
    # 2. Agent 独立目录（优先）
    # 遍历 ~/.aim/agents/ 下的所有目录
    if AGENTS_DIR.exists():
        for agent_dir in AGENTS_DIR.iterdir():
            if agent_dir.is_dir():
                secret_path = agent_dir / "secrets" / f"{agent_id}.secret"
                if secret_path.exists():
                    return secret_path.read_text().strip()
    
    # 3. 全局 secrets 目录
    global_secret = SECRET_DIR / f"{agent_id}.secret"
    if global_secret.exists():
        return global_secret.read_text().strip()
    
    # 4. 环境变量
    return os.environ.get("AIM_SECRET", "")


def append_jsonl(path: Path, data: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


# ── AIM Agent ──────────────────────────────────────────────

class AIMAgent:
    """轻量 AIM 客户端 — 只负责收发消息，处理逻辑由回调脚本完成"""

    def __init__(self, agent_id: str, server: str, secret: str,
                 callback: str = "", channel: str = "main"):
        self.agent_id = agent_id
        self.server = server
        self.secret = secret
        self.callback = callback
        self.channel = channel
        self._ws = None
        self._running = False

    async def run(self):
        """主循环：连接 → 认证 → 监听消息"""
        self._running = True
        while self._running:
            try:
                async with ws_connect(self.server, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    # 认证
                    auth = build_auth_payload(self.agent_id, self.secret, self.channel)
                    await ws.send(json.dumps(auth))
                    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if resp.get("cmd") != "auth_ok":
                        log.error(f"认证失败: {resp.get('reason', '')}")
                        await asyncio.sleep(5)
                        continue
                    log.info(f"✅ 已连接: {self.agent_id} | 服务端: {self.server}")
                    # 启动心跳任务
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    try:
                        # 监听消息
                        async for raw in ws:
                            try:
                                data = json.loads(raw)
                                # 异步处理消息，不阻塞读循环
                                asyncio.create_task(self._handle_incoming_safe(data))
                            except json.JSONDecodeError:
                                continue
                    finally:
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass
            except (ConnectionRefusedError, OSError) as e:
                log.warning(f"连接失败: {e}，5秒后重连...")
                await asyncio.sleep(5)
            except websockets.ConnectionClosed:
                log.info("连接断开，重连中...")
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"异常: {e}")
                await asyncio.sleep(5)

    async def _heartbeat_loop(self):
        """心跳循环 — 每 10 秒发送一次心跳"""
        while True:
            try:
                await asyncio.sleep(10)
                if self._ws:
                    heartbeat = {
                        "cmd": "heartbeat",
                        "agent_id": self.agent_id,
                        "status": "online",
                        "load": {},
                        "timestamp": int(time.time())
                    }
                    try:
                        await self._ws.send(json.dumps(heartbeat))
                        log.debug(f"💓 心跳已发送")
                    except Exception as e:
                        log.warning(f"💓 心跳发送失败: {e}")
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.debug(f"心跳发送异常: {e}")

    async def _handle_incoming_safe(self, data: dict):
        """安全处理消息（异常不传播）"""
        try:
            await self._handle_incoming(data)
        except Exception as e:
            log.error(f"消息处理异常: {e}")

    async def _handle_incoming(self, data: dict):
        """处理收到的消息"""
        cmd = data.get("cmd", "")
        # 只处理消息类型
        if cmd in ("message",):
            msg_data = data.get("msg", data)
            from_id = msg_data.get("from_id", msg_data.get("from", ""))
            content = msg_data.get("content", "")
            msg_id = msg_data.get("msg_id", str(uuid.uuid4())[:8])
            log.info(f"📨 来自 {from_id}: {content[:60]}")
            # 归档
            append_jsonl(MSG_FILE, {
                "ts": time.time(), "from": from_id, "to": self.agent_id,
                "content": content, "msg_id": msg_id, "cmd": "message",
            })
            # 推送 status_feedback: task_start
            await self._send_feedback(msg_id, "task_start", "running",
                                      f"开始处理来自 {from_id} 的消息", content=content)
            # 调用回调脚本（如果有）
            if self.callback:
                reply = await self._run_callback(from_id, content)
                if reply:
                    await self._send_reply(from_id, reply, msg_id)
                await self._send_feedback(msg_id, "task_end",
                                          "completed" if reply else "error",
                                          "处理完成" if reply else "处理失败（无回复）",
                                          content=reply or "")
            else:
                # 没有回调脚本，自动回复
                await self._send_reply(from_id, f"[ACK] 已收到: {content[:30]}", msg_id)
                await self._send_feedback(msg_id, "task_end", "completed", "自动ACK回复",
                                          content=f"[ACK] 已收到")

        elif cmd == "ack":
            log.info(f"📤 送达确认: {data.get('msg_id', '')[:12]}")

        elif cmd == "heartbeat_ack":
            pass  # 心跳确认，忽略

        elif cmd == "shutdown":
            log.info(f"🔌 服务端关闭: {data.get('reason', '')}")
            self._running = False

    async def _run_callback(self, from_id: str, content: str) -> str:
        """调用外部处理脚本"""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.callback,
                from_id, content,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                return stdout.decode().strip()
            else:
                log.error(f"回调失败: exit={proc.returncode} {stderr.decode()[:200]}")
                return ""
        except asyncio.TimeoutError:
            log.error("回调超时 (120s)")
            return ""
        except Exception as e:
            log.error(f"回调异常: {e}")
            return ""

    async def _send_feedback(self, session_id: str, step: str, status: str,
                              progress: str = "", content: str = ""):
        """推送 status_feedback"""
        if not self._ws:
            return
        try:
            payload = {
                "cmd": "status_feedback",
                "session_id": session_id,
                "step": step,
                "status": status,
                "progress": progress,
                "duration_ms": 0,
            }
            if content:
                payload["content"] = content
            await self._ws.send(json.dumps(payload))
        except Exception:
            pass

    async def _send_reply(self, to_id: str, content: str, msg_id: str = ""):
        """发送回复"""
        if not self._ws:
            return
        try:
            payload = {
                "cmd": "send",
                "to": to_id,
                "content": content,
                "msg_id": msg_id or str(uuid.uuid4())[:8],
                "channel": self.channel,
            }
            await self._ws.send(json.dumps(payload))
            log.info(f"📤 回复 {to_id}: {content[:40]}")
        except Exception as e:
            log.error(f"发送失败: {e}")


# ── 注册流程 ──────────────────────────────────────────────

async def register_agent(server: str, agent_name: str, framework: str,
                          operator_name: str = "") -> dict:
    """向 AIM Server 注册新 Agent"""
    async with ws_connect(server, open_timeout=10) as ws:
        payload = build_register_payload(agent_name, framework, operator_name)
        await ws.send(json.dumps(payload))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        return resp


# ── 主入口 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AIM 轻量客户端")
    parser.add_argument("--agent-id", help="Agent ID (如 ZS0003)")
    parser.add_argument("--server", default=os.environ.get("AIM_SERVER_URL", DEFAULT_SERVER),
                        help="AIM 服务端地址")
    parser.add_argument("--register", action="store_true",
                        help="注册模式：向服务端注册新 Agent")
    parser.add_argument("--agent-name", default="MyAgent", help="注册时的 Agent 名称")
    parser.add_argument("--framework", default="custom", help="框架名称")
    parser.add_argument("--operator", default="", help="操作人名称")
    parser.add_argument("--callback", default="",
                        help="消息处理回调脚本路径 (收到消息时调用)")
    parser.add_argument("--secret", default="",
                        help="密钥（不传则从 ~/.aim/secrets/{agent_id}.secret 读取）")
    parser.add_argument("--save-secret", action="store_true",
                        help="注册成功后保存密钥到 ~/.aim/secrets/")

    args = parser.parse_args()

    if args.register:
        # 注册模式
        print(f"📝 正在向 {args.server} 注册新 Agent: {args.agent_name}...")
        result = asyncio.run(register_agent(
            args.server, args.agent_name, args.framework, args.operator
        ))
        if result.get("cmd") == "register_ok":
            agent_id = result["agent_id"]
            agent_secret = result["agent_secret"]
            print(f"✅ 注册成功!")
            print(f"   Agent ID:  {agent_id}")
            print(f"   Secret:    {agent_secret}")
            print(f"   剩余配额:  {result.get('agents_remaining', '?')}")
            if args.save_secret:
                secret_path = SECRET_DIR / f"{agent_id}.secret"
                secret_path.write_text(agent_secret)
                os.chmod(secret_path, 0o600)
                print(f"   密钥已保存: {secret_path}")
            print()
            print(f"启动命令:")
            print(f"  python3 {__file__} --agent-id {agent_id} --server {args.server} --callback /path/to/handler.sh")
        else:
            print(f"❌ 注册失败: {result.get('reason', '')}")
            if result.get("failed_check"):
                print(f"   未通过检查: {result['failed_check']}")
        return

    # 运行模式
    agent_id = args.agent_id or os.environ.get("AIM_AGENT_ID", "")
    if not agent_id:
        print("ERROR: 需要 --agent-id 或设置 AIM_AGENT_ID 环境变量")
        sys.exit(1)

    # 获取密钥（支持多目录查找）
    secret = find_secret(agent_id, args.secret)
    if not secret:
        print(f"ERROR: 找不到 {agent_id} 的密钥")
        print(f"  查找目录：")
        print(f"    - ~/.aim/agents/agent-XX/secrets/{agent_id}.secret")
        print(f"    - ~/.aim/secrets/{agent_id}.secret")
        print(f"  或通过 --secret 参数传入")
        print(f"  或通过 AIM_SECRET 环境变量设置")
        sys.exit(1)

    agent = AIMAgent(agent_id, args.server, secret, args.callback)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        print("\n👋 Bye")


if __name__ == "__main__":
    main()
