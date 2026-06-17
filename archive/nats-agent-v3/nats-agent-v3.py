#!/usr/bin/env python3
"""
AIM Agent NATS V3 — "直通"架构

去掉 .aim-queue/ 和 .aim-replies/ 文件中间层，
nats-agent 直接调框架 adapter，NATS JetStream 做消息排队和持久化。

架构:
  NATS ──→ nats-agent-v3 ──→ call_adapter() ──→ adapter.sh ──→ 框架 AI
    ↑                                                                    ↓
    └──────────────────── 同一个 NATS 主题 ─────────────────────────┘

启动:
    python3 nats-agent-v3.py --agent-id ZS0002 --config ~/.aim/agents/ZS0002/config.json

V2/V3 并行:
    V2 文件队列路径完全不删
    V3 以 --mode direct 独立启动，互不干扰
    --mode legacy 回退到 V2 文件队列模式（降级用）
"""

import argparse
import asyncio
import json
import atexit
import fcntl
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
from typing import Optional

# ── 单实例互斥 ───────────────────────────────────────────────

LOCK_DIR = Path.home() / ".aim" / "run"
LOCK_DIR.mkdir(parents=True, exist_ok=True)


class SingleInstance:
    """进程互斥锁：同一 agent_id 只能运行一个 V3 实例"""

    def __init__(self, agent_id: str):
        self.lock_file = LOCK_DIR / f"nats-agent-v3-{agent_id}.lock"
        self.fp = None

    def acquire(self) -> bool:
        try:
            self.fp = open(self.lock_file, "w")
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            return True
        except (IOError, OSError):
            if self.fp:
                self.fp.close()
                self.fp = None
            return False

    def release(self):
        if self.fp:
            try:
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
                self.fp.close()
                self.lock_file.unlink(missing_ok=True)
            except Exception:
                pass
            self.fp = None

# SDK
SDK_PATH = Path.home() / ".aim" / "bin" / "aim_nats_sdk.py"
if SDK_PATH.exists():
    sys.path.insert(0, str(SDK_PATH.parent))

try:
    from aim_nats_sdk import AIMNATSClient
except ImportError:
    print("ERROR: 未找到 aim_nats_sdk.py")
    sys.exit(1)

# Phase 0: AIM Client Queue + Scheduler
AIM_CLIENT_PATH = Path.home() / ".openclaw" / "workspace"
sys.path.insert(0, str(AIM_CLIENT_PATH))
from aim_client import MessageQueue, Scheduler, HealthProbe, Message, AgentState, StateReport

# call_adapter
ADAPTER_PATH = Path(__file__).parent / "call_adapter.py"
sys.path.insert(0, str(ADAPTER_PATH.parent))
from call_adapter import call_adapter, SUCCESS, RETRY, DEGRADE, HUMAN, ERROR

HOME = Path.home()
AIM_DIR = HOME / ".aim"
LOG_DIR = AIM_DIR / "logs"
DATA_DIR = AIM_DIR / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── 配置 ──────────────────────────────────────────────────

DEFAULT_TIMEOUT = 120
HEARTBEAT_INTERVAL = 60
PING_INTERVAL = 20
MAX_CONCURRENT = 3
DEGRADE_DIR = HOME / ".aim" / "degrade"  # 降级文件队列
DEGRADE_TTL = 30 * 60  # 30 分钟
JETSTREAM_SUBJECT = "aim.messages.v3"


# ── 日志 ──────────────────────────────────────────────────

def setup_logging(agent_id: str) -> logging.Logger:
    log_file = LOG_DIR / f"nats-agent-v3-{agent_id}.log"
    log = logging.getLogger(f"aim-v3-{agent_id}")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fh = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
        log.addHandler(fh)
        # 不再添加 StreamHandler(stderr)：nohup 2>&1 会把 stderr
        # 重定向到同个日志文件，导致每条日志重复输出两次
    return log


# ── 降级文件队列 ──────────────────────────────────────────

def write_degrade_queue(msg_id: str, from_id: str, content: str, meta: dict = None):
    """写入降级文件队列（仅当 direct 模式失败时）"""
    degrade_dir = DEGRADE_DIR / "queue"
    degrade_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "msg_id": msg_id,
        "from": from_id,
        "content": content,
        "ts": time.time(),
        "meta": meta or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = degrade_dir / f"{msg_id}.json"
    try:
        path.write_text(json.dumps(data, ensure_ascii=False))
        return True
    except Exception as e:
        log.error(f"写入降级队列失败: {e}")
        return False


# ── AIMAgentNATSV3 ────────────────────────────────────────


class AIMAgentNATSV3:
    """AIM Agent NATS V3 — 直通架构"""

    def __init__(
        self,
        agent_id: str,
        config_path: str,
        mode: str = "direct",
        nats_url: str = None,
    ):
        self.agent_id = agent_id
        self.config_path = config_path
        self.mode = mode  # "direct" 或 "legacy"
        self.log = setup_logging(agent_id)

        # 加载配置
        self.config = self._load_config()
        self.nats_url = nats_url or self.config.get("nats_url", "nats://127.0.0.1:4222")

        self.log.info(f"🚀 AIM Agent NATS V3 初始化 (mode={mode})")
        self.log.info(f"   Agent: {agent_id}")
        self.log.info(f"   NATS: {self.nats_url}")
        self.log.info(f"   Framework: {self.config.get('framework', 'unknown')}")
        self.log.info(f"   Adapter: {self.config.get('adapter_cmd', '未配置')}")

        # NATS 客户端
        creds = self._resolve_creds()
        self.client = AIMNATSClient(
            agent_id=agent_id,
            server=self.nats_url,
            credentials=creds,
        )

        # 运行时
        self._running = False
        self._active_tasks: dict = {}
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        # Phase 0: Queue + Scheduler
        adapter_cmd = self.config.get("adapter_cmd", "")
        self.queue = MessageQueue(capacity=self.config.get("queue_capacity", 1000))
        self.scheduler = Scheduler(
            processing_timeout=self.config.get("adapter_timeout", 120.0),
        )
        self.health_probe = HealthProbe(
            health_cmd=f"{adapter_cmd} health" if adapter_cmd else "true",
            timeout=10.0,   # ZS0003 adapter health ~4.8s，留足余量
        )
        self.log.info(f"🧪 Phase 0: Queue+Scheduler 已嵌入 (capacity={self.queue.capacity})")

    def _load_config(self) -> dict:
        """加载 Agent 配置"""
        path = Path(self.config_path).expanduser()
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception as e:
                self.log.warning(f"配置加载失败: {e}")
        return {}

    def _resolve_creds(self) -> Optional[str]:
        """解析 JWT credentials"""
        # 优先用 agents 目录下的 creds 文件
        creds_path = AIM_DIR / "agents" / self.agent_id / "aim.creds"
        if creds_path.exists():
            return str(creds_path)
        # 从全局配置读取
        cfg_path = AIM_DIR / "config" / "aim.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text())
                agents = cfg.get("agents", {})
                if self.agent_id in agents:
                    info = agents[self.agent_id]
                    if isinstance(info, dict) and "creds_path" in info:
                        cp = Path(info["creds_path"]).expanduser()
                        if cp.exists():
                            return str(cp)
            except Exception:
                pass
        return None

    # ── 消息处理 ──────────────────────────────────────────

    async def handle_message(self, envelope: dict):
        """Phase 0: Queue + Scheduler 流程"""
        msg_id = envelope.get("id", str(uuid.uuid4())[:12])
        from_id = envelope.get("from", "")
        payload = envelope.get("payload", {})
        content = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        raw_type = envelope.get("type", "dm")

        if not content:
            return

        if from_id == self.agent_id:
            self.log.debug(f"🚫 跳过自己的消息: {msg_id}")
            return

        self._archive(envelope)

        self.log.info(f"⚙️ [{msg_id[:8]}] {raw_type} from={from_id}: {content[:80]}")

        # Observer 事件
        try:
            await self.client.emit_state_report(
                "received",
                active_sessions=1,
                queue_depth=self.queue.size() + 1,
                msg_id=msg_id,
                detail=f"收到来自 {from_id} 的消息: {content[:50]}"
            )
        except Exception:
            pass

        # Phase 0: 入队 → Scheduler 决定投递时机
        aim_msg = Message(
            msg_id=msg_id,
            from_id=from_id,
            msg_type=raw_type,
            content=content,
            raw_envelope=envelope,
        )
        self.queue.enqueue(aim_msg)
        self.scheduler.on_message_enqueued()

        # Phase 0 fix: 不在 NATS 回调中调 _try_dispatch
        # nats-py callback 是 await 同步执行的，调用会阻塞后续消息递送
        # 调度交给健康探针循环（独立 task），在 idle + q>0 时触发

    async def _try_dispatch(self):
        """Scheduler 驱动的消息投递循环（由健康探针循环调用）"""
        while self._running:
            if not self.scheduler.should_dispatch():
                pass  # should_dispatch=False
                break

            msg = self.queue.dequeue()
            if msg is None:
                pass  # dequeue=None
                break

            self.scheduler.on_dispatch_started()
            self.log.info(f"📤 Scheduler 投递: {msg.msg_id[:8]} from={msg.from_id} (queue={self.queue.size()})")
            try:
                await self._process_message_direct(msg)
                self.queue.ack(msg.msg_id)
            except Exception as e:
                self.log.error(f"处理失败 [{msg.msg_id[:8]}]: {e}")
                self.queue.nack(msg.msg_id, str(e))
            finally:
                self.scheduler.on_processing_done()

    async def _process_message_direct(self, msg: Message):
        """直通模式：直接调 adapter，不走文件"""
        result = await call_adapter(
            message=msg.content,
            from_id=msg.from_id,
            config=self.config,
        )

        status = result.get("status")
        reply = result.get("reply", "")
        detail = result.get("detail", "")

        if status == SUCCESS:
            # Observer 事件：处理完成
            try:
                await self.client.emit_state_report(
                    "completed",
                    active_sessions=0,
                    queue_depth=0,
                    msg_id=msg.msg_id,
                    detail=f"已回复 {msg.from_id}" if reply else f"空回复（不发送）"
                )
            except Exception:
                pass

            # 成功：发回 NATS
            if reply:
                msg_type = msg.raw_envelope.get("type", "dm")

                # 安全校验：不允许把群聊消息发到私聊信道
                if msg_type == "grp":
                    # 群聊回复：从 meta 取 group，兜底用 "grp_trio"
                    group = msg.raw_envelope.get("meta", {}).get("group", "grp_trio")
                    await self.client.send_grp(group, reply)
                    self.log.info(f"✅ 回复群聊 {group}: {reply[:60]}...")
                elif msg_type == "dm":
                    await self.client.send_dm(msg.from_id, reply, reply_to=msg.msg_id)
                    self.log.info(f"✅ 回复 {msg.from_id}: {reply[:60]}...")
                else:
                    # 未知类型：兜底到私聊
                    self.log.warning(f"⚠️ 未知消息类型 {msg_type}，降级为私聊回复")
                    await self.client.send_dm(msg.from_id, reply, reply_to=msg.msg_id)
            else:
                self.log.debug(f"🤫 [{msg.msg_id[:8]}] 空回复，不发送")

        elif status == DEGRADE:
            # 降级：写入文件队列
            group = msg.raw_envelope.get("meta", {}).get("group", "")
            meta = {"type": msg.raw_envelope.get("type", "dm"), "group": group} if group else {}
            write_degrade_queue(msg.msg_id, msg.from_id, msg.content, meta)
            self.log.warning(f"⬇️ [{msg.msg_id[:8]}] 降级到文件队列: {detail}")

        elif status == HUMAN:
            # 需人工介入
            self.log.error(f"🆘 [{msg.msg_id[:8]}] 需人工介入: {detail}")
            # TODO: 通知大哥

        elif status == RETRY:
            # 可重试：优先重新入队（轻量），超过上限再降级
            MAX_RETRIES = 3
            if msg.retry_count < MAX_RETRIES:
                msg.retry_count += 1
                self.queue.enqueue(msg)
                self.log.info(f"🔄 [{msg.msg_id[:8]}] RETRY #{msg.retry_count}/{MAX_RETRIES} → 重新入队 (detail={detail})")
            else:
                group = msg.raw_envelope.get("meta", {}).get("group", "")
                meta = {"type": msg.raw_envelope.get("type", "dm"), "group": group} if group else {}
                write_degrade_queue(msg.msg_id, msg.from_id, msg.content, meta)
                self.log.warning(f"⬇️ [{msg.msg_id[:8]}] RETRY #{msg.retry_count} 超限，降级: {detail}")

        elif status == ERROR:
            # 错误：降级
            group = msg.raw_envelope.get("meta", {}).get("group", "")
            meta = {"type": msg.raw_envelope.get("type", "dm"), "group": group} if group else {}
            write_degrade_queue(msg.msg_id, msg.from_id, msg.content, meta)
            self.log.warning(f"⬇️ [{msg.msg_id[:8]}] 错误，降级: {detail}")

    def _archive(self, envelope: dict):
        """归档消息到 JSONL"""
        archive_file = DATA_DIR / f"nats_v3_messages_{self.agent_id}.jsonl"
        try:
            with open(archive_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    # ── NATS 回调 ──────────────────────────────────────────

    async def _on_dm_msg(self, envelope: dict, raw_msg):
        """私聊消息回调"""
        try:
            self.log.debug(f"< 私聊: {envelope.get('from')} → "
                           f"{str(envelope.get('payload', {}).get('text', ''))[:80]}")
            await self.handle_message(envelope)
        except Exception as e:
            self.log.error(f"私聊处理失败: {e}")

    async def _on_grp_msg(self, envelope: dict, raw_msg):
        """群聊消息回调"""
        try:
            from_id = envelope.get("from", "")
            content = envelope.get("payload", {}).get("text", "")
            self.log.debug(f"< 群聊: {from_id} → {content[:80]}")
            await self.handle_message(envelope)
        except Exception as e:
            self.log.error(f"群聊处理失败: {e}")

    # ── 生命周期 ──────────────────────────────────────────

    async def run(self):
        """启动 V3 Agent（单实例互斥）"""
        # 单实例检查
        self._lock = SingleInstance(self.agent_id)
        if not self._lock.acquire():
            self.log.error(f"❌ {self.agent_id} V3 已有实例在运行 (lock={LOCK_DIR / f'nats-agent-v3-{self.agent_id}.lock'})")
            print(f"ERROR: {self.agent_id} V3 已有实例在运行", file=sys.stderr)
            sys.exit(1)
        atexit.register(self._lock.release)
        # 连接 NATS
        await self.client.connect()
        self.log.info(f"🟢 [{self.agent_id}] 已连接到 NATS: {self.nats_url}")

        # 注册（非阻塞，失败不阻塞 Agent 启动）
        self.log.info(f"📝 [{self.agent_id}] 等待 NATS 稳定 (5s)...")
        await asyncio.sleep(5)
        try:
            result = await self.client.register(
                agent_name=self.config.get("agent_name", ""),
                framework=self.config.get("framework", ""),
                timeout=10,
            )
            self.log.info(f"📝 [{self.agent_id}] 注册成功: {result.get('status', 'ok')}")
        except TimeoutError:
            self.log.warning(f"📝 [{self.agent_id}] 注册超时（Server registry 未响应），降级继续")
        except NotImplementedError:
            self.log.info(f"📝 [{self.agent_id}] SDK 无 register（旧版 SDK），跳过注册")
        except Exception as e:
            self.log.warning(f"📝 [{self.agent_id}] 注册失败 ({e})，降级继续")

        # 订阅
        await self.client.subscribe_dm(self._on_dm_msg)
        self.log.info(f"📬 已订阅私聊: aim.dm.{self.agent_id}")

        for gid in ["grp_trio"]:
            await self.client.subscribe_grp(gid, self._on_grp_msg)
            self.log.info(f"📬 已订阅群聊: aim.grp.{gid}")

        # 心跳
        self._running = True

        # Agent Card 加载（P0 Schema v1）
        try:
            identity_path = HOME / ".aim" / "agents" / self.agent_id / "identity.json"
            # 从 adapter.sh info 获取 runtime 信息
            adapter_cmd = self.config.get("adapter_cmd", "")
            if adapter_cmd:
                import subprocess
                result = subprocess.run(
                    f"{adapter_cmd} info", shell=True, capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    info = json.loads(result.stdout)
                    if info:
                        self.client.set_runtime_info(info)
            card = await self.client.load_agent_card(
                card_path=str(identity_path) if identity_path.exists() else ""
            )
            # KV publish: 非阻塞尝试（Server 无 KV 则静默跳过）
            try:
                await asyncio.wait_for(
                    self.client.publish_agent_card(card), timeout=5
                )
            except (asyncio.TimeoutError, Exception):
                self.log.debug("Agent Card KV publish skipped (KV unavailable)")
        except Exception as e:
            self.log.warning(f"Agent Card setup failed: {e}")

        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._health_probe_loop())  # Phase 0: 健康探针

        self.log.info(f"✅ {self.agent_id} V3 启动完成 (mode={self.mode})")

        # 保持运行
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self):
        """停止"""
        self._running = False
        for task in self._active_tasks.values():
            task.cancel()
        try:
            await self.client.disconnect()
        except Exception:
            pass
        self.log.info(f"⏹ {self.agent_id} V3 已停止")

    async def _health_probe_loop(self):
        """Phase 0: 健康探针循环"""
        await asyncio.sleep(5)  # 先等 NATS 稳定
        while self._running:
            try:
                report = await self.health_probe.probe()
                self.scheduler.update_state(report)
                self.log.debug(f"💓 Health: {report.status.value} (q={self.queue.size()})")

                # 如果恢复 IDLE 且有 pending，尝试投递
                if self.scheduler.state == AgentState.IDLE and self.queue.size() > 0:
                    await self._try_dispatch()
            except Exception as e:
                self.log.error(f"Health probe error: {e}")

            interval = self.scheduler.get_probe_interval()
            await asyncio.sleep(interval)

    async def _heartbeat_loop(self):
        """心跳"""
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await self.client.emit_state_report(
                    "heartbeat", msg_id="",
                    active_sessions=1, queue_depth=0,
                    detail=f"{self.agent_id} V3 alive"
                )
            except Exception:
                pass

    # ── 降级消费（在 legacy 模式下读取文件队列）────────────

    async def consume_degrade_queue(self):
        """消费降级文件队列（定期扫描，可选）"""
        queue_dir = DEGRADE_DIR / "queue"
        if not queue_dir.exists():
            return

        while self._running:
            try:
                for f in sorted(queue_dir.iterdir()):
                    if not f.name.endswith(".json"):
                        continue
                    try:
                        data = json.loads(f.read_text())
                        age = time.time() - data.get("ts", 0)
                        if age > DEGRADE_TTL:
                            f.unlink(missing_ok=True)
                            continue
                        # 重新尝试处理
                        result = await call_adapter(
                            message=data["content"],
                            from_id=data["from"],
                            config=self.config,
                        )
                        if result.get("status") == SUCCESS:
                            reply = result["reply"]
                            if reply:
                                await self.client.send_dm(
                                    data["from"], reply, reply_to=data["msg_id"]
                                )
                            f.unlink(missing_ok=True)
                            self.log.info(f"⬆️ 降级队列恢复: {data['msg_id'][:8]}")
                    except Exception as e:
                        self.log.debug(f"降级队列处理失败: {e}")
            except Exception:
                pass
            await asyncio.sleep(30)


# ── 入口 ──────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AIM Agent NATS V3 — 直通架构")
    parser.add_argument("--agent-id", required=True, help="Agent ID")
    parser.add_argument("--config", default="", help="config.json 路径")
    parser.add_argument("--mode", default="direct", choices=["direct", "legacy"],
                        help="运行模式: direct(直通) / legacy(文件队列降级)")
    parser.add_argument("--nats-url", default="", help="NATS 地址")
    args = parser.parse_args()

    # 默认 config 路径
    config_path = args.config or str(AIM_DIR / "agents" / args.agent_id / "config.json")

    agent = AIMAgentNATSV3(
        agent_id=args.agent_id,
        config_path=config_path,
        mode=args.mode,
        nats_url=args.nats_url,
    )

    def _signal_handler():
        asyncio.create_task(agent.stop())

    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        asyncio.run(agent.stop())


if __name__ == "__main__":
    main()
