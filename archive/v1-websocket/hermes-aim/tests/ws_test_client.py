"""
AIM P3-1 测试客户端 — WebSocket 直连 Server
============================================
用途：T3.5（持久化验证）、T5（离线队列满）、T8（连接池满）、T9（极端负载）

用法：
  # T3.5: 发5条消息给离线Agent，验证持久化
  python ws_test_client.py send --to ZS0002 --count 5 --interval 0.5

  # T5: 发5100条消息给离线Agent（触发队列满）
  python ws_test_client.py send --to ZS0002 --count 5100 --interval 0.1

  # T8: 同一Agent创建6个main channel连接
  python ws_test_client.py multi-connect --agent ZS0003 --channel main --count 6

  # T9: 30秒内发100条消息
  python ws_test_client.py send --to ZS0002 --count 100 --interval 0.3 --duration-limit 30

  # T3.5 验证: 检查 messages.jsonl 中的特定消息
  python ws_test_client.py verify --msg-id <msg_id>

  # 清理: 重置离线队列
  python ws_test_client.py reset-queue --agent ZS0002

环境变量:
  AIM_SERVER_URL=ws://localhost:18900  (默认)
  AIM_AGENT_ID=ZS0003                  (用作发送方，默认)
  AIM_AGENT_SECRET=xxx                 (HMAC认证密钥)
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import string
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ws_test_client")

# ── 默认配置 ───────────────────────────────────────

SERVER_URL = os.environ.get("AIM_SERVER_URL", "ws://localhost:18900")
AGENT_ID = os.environ.get("AIM_AGENT_ID", "ZS0003")
AGENT_SECRET = os.environ.get("AIM_AGENT_SECRET", "")
CHANNEL = os.environ.get("AIM_CHANNEL", "script")

# AIM Server 数据目录
AIM_DATA_DIR = Path.home() / ".hermes" / "aim" / "data"

# ── HMAC 工具 ──────────────────────────────────────

def make_signature(agent_id: str, timestamp: int, secret: str) -> str:
    """生成 HMAC-SHA256 签名"""
    msg = f"{agent_id}:{timestamp}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def make_msg_signature(agent_id: str, msg_id: str, content: str, timestamp: int, secret: str) -> str:
    """生成消息签名"""
    raw = f"{agent_id}:{msg_id}:{timestamp}:{content}"
    return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


def random_msg_id() -> str:
    """生成随机消息ID"""
    return f"test_{int(time.time())}_{random.randint(1000, 9999)}"


# ── WebSocket 客户端 ───────────────────────────────

class TestWsClient:
    """测试用 WebSocket 客户端"""

    def __init__(self, agent_id: str = AGENT_ID, channel: str = CHANNEL,
                 secret: str = AGENT_SECRET, server_url: str = SERVER_URL):
        self.agent_id = agent_id
        self.channel = channel
        self.secret = secret
        self.server_url = server_url
        self.ws = None
        self._connected = False
        self._auth_ok = False

    async def connect(self) -> bool:
        """连接并认证"""
        try:
            import websockets
            self.ws = await websockets.connect(self.server_url, ping_interval=30, ping_timeout=10)
        except ImportError:
            log.error("需要 websockets 库: pip install websockets")
            return False
        except Exception as e:
            log.error(f"连接失败: {e}")
            return False

        self._connected = True
        return await self._authenticate()

    async def _authenticate(self) -> bool:
        """HMAC 认证"""
        ts = int(time.time())
        auth_data = {
            "cmd": "auth",
            "agent_id": self.agent_id,
            "channel": self.channel,
            "handler": False,  # 测试客户端不设 handler
        }
        if self.secret:
            auth_data["timestamp"] = ts
            auth_data["signature"] = make_signature(self.agent_id, ts, self.secret)

        await self.ws.send(json.dumps(auth_data))
        resp = json.loads(await self.ws.recv())

        if resp.get("cmd") == "auth_ok":
            self._auth_ok = True
            log.info(f"✅ 认证成功: {self.agent_id} ({self.channel})")
            return True
        else:
            log.error(f"❌ 认证失败: {resp.get('reason', 'unknown')}")
            return False

    async def send_message(self, to_id: str, content: str = None, msg_id: str = None) -> bool:
        """发送消息"""
        if not self._auth_ok:
            log.error("未认证，请先 connect()")
            return False

        if content is None:
            content = f"测试消息 {time.strftime('%H:%M:%S')} [{random.randint(0, 9999):04d}]"

        msg_id = msg_id or random_msg_id()
        ts = int(time.time())

        msg = {
            "cmd": "send",
            "to": to_id,
            "content": content,
            "msg_id": msg_id,
            "channel": self.channel,
            "timestamp": ts,
        }

        if self.secret:
            msg["signature"] = make_msg_signature(self.agent_id, msg_id, content, ts, self.secret)

        await self.ws.send(json.dumps(msg))

        # 等待 ack
        try:
            ack = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=5.0))
            if ack.get("delivered"):
                return True
            elif ack.get("cmd") == "ack":
                return True
            else:
                log.warning(f"ACK 异常: {ack}")
                return False
        except asyncio.TimeoutError:
            log.warning(f"ACK 超时: {msg_id[:12]}")
            return False

    async def close(self):
        """关闭连接"""
        if self.ws:
            await self.ws.close()
        self._connected = False
        self._auth_ok = False

    async def listen(self, timeout: float = 30.0, callback=None):
        """监听消息"""
        if not self._auth_ok:
            log.error("未认证")
            return

        deadline = time.time() + timeout
        count = 0
        while time.time() < deadline:
            try:
                data = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=5.0))
                count += 1
                if callback:
                    await callback(data)
                else:
                    cmd = data.get("cmd", "?")
                    if cmd == "message":
                        msg = data.get("msg", {})
                        log.info(f"📩 [{msg.get('from_id','?')}] {msg.get('content','')[:50]}")
                    elif cmd == "status_feedback_ack":
                        pass
                    else:
                        log.info(f"📨 cmd={cmd}")
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.error(f"监听异常: {e}")
                break
        log.info(f"监听结束 (收到 {count} 条消息)")


# ── 消息持久化验证工具 ─────────────────────────────

def check_messages_jsonl(to_id: str, count: int = 5, timeout: float = 10.0) -> list:
    """检查 messages.jsonl 中发给指定 Agent 的最新 N 条消息"""
    msg_file = AIM_DATA_DIR / "messages.jsonl"
    if not msg_file.exists():
        log.error(f"messages.jsonl 不存在: {msg_file}")
        return []

    # 读取最后 N 条发给 to_id 的消息
    found = []
    try:
        with open(msg_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get("to_id") == to_id:
                        found.append(msg)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.error(f"读取 messages.jsonl 失败: {e}")
        return []

    return found[-count:]


def check_offline_queue(agent_id: str) -> int:
    """检查离线队列中的消息数"""
    qfile = AIM_DATA_DIR / f"offline_{agent_id}.jsonl"
    if not qfile.exists():
        return 0
    count = 0
    try:
        with open(qfile, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
    except Exception as e:
        log.error(f"读取离线队列失败: {e}")
    return count


def reset_offline_queue(agent_id: str):
    """清空离线队列"""
    qfile = AIM_DATA_DIR / f"offline_{agent_id}.jsonl"
    if qfile.exists():
        qfile.unlink()
        log.info(f"🗑️ 清空离线队列: {qfile}")


# ── 命令行入口 ─────────────────────────────────────

async def cmd_send(args):
    """T3.5: 发送多条消息"""
    client = TestWsClient(agent_id=args.from_agent, channel=CHANNEL)
    if not await client.connect():
        return

    log.info(f"📤 发送 {args.count} 条消息到 {args.to} (间隔 {args.interval}s)")
    success = 0
    start = time.time()
    for i in range(args.count):
        content = f"测试消息 #{i+1} — {time.strftime('%H:%M:%S')}"
        ok = await client.send_message(args.to, content=content)
        if ok:
            success += 1
        if args.interval > 0:
            await asyncio.sleep(args.interval)
        # 检查时长限制
        if args.duration_limit and (time.time() - start) > args.duration_limit:
            log.info(f"达到时长限制，停止发送 (已发 {i+1} 条)")
            break

    elapsed = time.time() - start
    await client.close()
    log.info(f"✅ 发送完成: {success}/{args.count} 成功 ({elapsed:.1f}s)")

    # 验证落盘
    if args.verify:
        persisted = check_messages_jsonl(args.to, count=min(10, args.count))
        offline = check_offline_queue(args.to)
        log.info(f"📊 验证: messages.jsonl 中最近 {len(persisted)} 条 | 离线队列 {offline} 条")


async def cmd_multi_connect(args):
    """T8: 创建多个连接"""
    clients = []
    log.info(f"🔗 创建 {args.count} 个 {args.channel} 连接 (agent={args.agent})")
    for i in range(args.count):
        client = TestWsClient(agent_id=args.agent, channel=args.channel)
        ok = await client.connect()
        if ok:
            clients.append(client)
            log.info(f"  [{i+1}] ✅ 连接成功")
        else:
            log.warning(f"  [{i+1}] ❌ 连接被拒")
        if i < args.count - 1:
            await asyncio.sleep(0.2)

    log.info(f"最终: {len(clients)}/{args.count} 连接成功")
    # 关闭所有
    for c in clients:
        await c.close()


async def cmd_verify(args):
    """验证 messages.jsonl 中的消息"""
    if args.msg_id:
        msg_file = AIM_DATA_DIR / "messages.jsonl"
        if not msg_file.exists():
            log.error("messages.jsonl 不存在")
            return
        with open(msg_file, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    m = json.loads(line)
                    if m.get("msg_id") == args.msg_id:
                        print(f"找到消息: {json.dumps(m, indent=2, ensure_ascii=False)}")
                        return
                except:
                    continue
        log.info(f"未找到 msg_id={args.msg_id}")
    else:
        # 显示最近的消息
        msg_file = AIM_DATA_DIR / "messages.jsonl"
        if not msg_file.exists():
            log.error("messages.jsonl 不存在")
            return
        with open(msg_file, "r") as f:
            lines = [l.strip() for l in f if l.strip()]
        recent = lines[-args.lines:] if args.lines > 0 else lines
        for line in recent:
            try:
                m = json.loads(line)
                print(f"[{m.get('from_id','?')}→{m.get('to_id','?')}] "
                      f"{m.get('content','')[:50]}  msg_id={m.get('msg_id','?')[:12]}")
            except:
                print(f"(parse error) {line[:80]}")


async def cmd_listen(args):
    """监听消息"""
    client = TestWsClient(agent_id=args.agent, channel=args.channel)
    if not await client.connect():
        return
    log.info(f"👂 监听 {args.timeout}s ...")
    await client.listen(timeout=args.timeout)
    await client.close()


def cmd_reset_queue(args):
    """清理离线队列"""
    reset_offline_queue(args.agent)
    # 也清理 test 残留的 offline_ZS0002.jsonl 等
    for f in AIM_DATA_DIR.glob(f"offline_*.jsonl"):
        print(f"  {f.name}")
    log.info("Done")


def main():
    parser = argparse.ArgumentParser(description="AIM P3-1 测试客户端")
    sub = parser.add_subparsers(dest="command", required=True)

    # send
    p = sub.add_parser("send", help="发送多条消息（T3.5 / T5 / T9）")
    p.add_argument("--to", required=True, help="目标 Agent ID")
    p.add_argument("--count", type=int, default=5, help="消息数量")
    p.add_argument("--interval", type=float, default=0.5, help="发送间隔（秒）")
    p.add_argument("--from-agent", default=AGENT_ID, help="发送方 Agent ID")
    p.add_argument("--verify", action="store_true", help="发送后验证落盘")
    p.add_argument("--duration-limit", type=float, default=None, help="最大时长（秒）")

    # multi-connect
    p = sub.add_parser("multi-connect", help="创建多个连接（T8）")
    p.add_argument("--agent", required=True, help="Agent ID")
    p.add_argument("--channel", default="main", help="Channel 名称")
    p.add_argument("--count", type=int, default=6, help="连接数")

    # verify
    p = sub.add_parser("verify", help="验证 messages.jsonl")
    p.add_argument("--msg-id", default="", help="按 msg_id 查找")
    p.add_argument("--lines", type=int, default=10, help="显示最近 N 条")

    # listen
    p = sub.add_parser("listen", help="监听消息（验证离线回放）")
    p.add_argument("--agent", default=AGENT_ID, help="监听 Agent ID")
    p.add_argument("--channel", default=CHANNEL, help="Channel")
    p.add_argument("--timeout", type=float, default=30.0, help="监听时长（秒）")

    # reset-queue
    p = sub.add_parser("reset-queue", help="清空离线队列")
    p.add_argument("--agent", required=True, help="Agent ID")

    args = parser.parse_args()

    if args.command == "send":
        asyncio.run(cmd_send(args))
    elif args.command == "multi-connect":
        asyncio.run(cmd_multi_connect(args))
    elif args.command == "verify":
        asyncio.run(cmd_verify(args))
    elif args.command == "listen":
        asyncio.run(cmd_listen(args))
    elif args.command == "reset-queue":
        cmd_reset_queue(args)


if __name__ == "__main__":
    main()
