#!/usr/bin/env python3.13
"""AIM Client — 统一通信终端

OAS 基础设施核心组件。
取代 nats-agent-v3.py 成为三方标准通信入口。

架构:
  aim-client (本进程)
    ├── Transport (NATS -> 可插拔，基于 SDK AIMNATSClient)
    ├── Identity (AgentCard + JWT)
    ├── Security (白名单 + 限流 + 认证链)
    ├── Queue+Scheduler+HealthProbe (Phase 0 内嵌)
    └── Adapter (call adapter.sh)

Phase 1 (当前):
  - 独立进程，launchd 保活
  - Transport 7 方法接口
  - Agent Card v1
  - Message/Task 分层 (AIMChat + AIMTask)
启动:
    python3 aim-client/main.py --agent-id ZS0001 --config ~/.aim/agents/ZS0001/config.json

依赖:
    - ~/.aim/bin/aim_nats_sdk.py (SDK)
    - ~/.openclaw/workspace/aim_client/ (types, queue, scheduler)
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import fcntl
import json
import logging
import os
import signal
import sys
import time
from collections import deque
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# -- 路径注入 --
SHARED_AIM = Path.home() / "shared" / "aim"
SDK_DIR = Path.home() / ".aim" / "bin"

for p in [SDK_DIR, SHARED_AIM]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from aim_client.types import (
    AIMChat, AIMTask, TaskStatus, AgentCard,
    AgentState, StateReport, DeliveryMode, Message,
)
from aim_client.queue import MessageQueue
from aim_client.scheduler import Scheduler
from aim_client.health_probe import HealthProbe
from aim_nats_sdk import load_global_config

# 619-06: 读取全局 VERSION 文件，不再写死
VERSION_FILE = SHARED_AIM / "VERSION"
try:
    _AIM_VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "unknown"
except Exception:
    _AIM_VERSION = "unknown"

# AIM Client 内部模块（绝对导入）
import sys
import importlib.util

# 先设置 sys.path
sys.path.insert(0, str(Path.home() / 'shared' / 'aim' / 'aim-client'))

# 动态导入 security
security_path = Path.home() / 'shared' / 'aim' / 'aim-client' / 'security.py'
security_spec = importlib.util.spec_from_file_location('security', security_path)
security_module = importlib.util.module_from_spec(security_spec)
sys.modules['security'] = security_module
security_spec.loader.exec_module(security_module)
SecurityManager = security_module.SecurityManager

# 动态导入 registry
registry_path = Path.home() / 'shared' / 'aim' / 'aim-client' / 'registry.py'
registry_spec = importlib.util.spec_from_file_location('registry', registry_path)
registry_module = importlib.util.module_from_spec(registry_spec)
sys.modules['registry'] = registry_module
registry_spec.loader.exec_module(registry_module)
Registry = registry_module.Registry

# -- 单实例互斥 --
LOCK_DIR = Path.home() / ".aim" / "run"
LOCK_DIR.mkdir(parents=True, exist_ok=True)


class SingleInstance:
    """文件锁 + PID 存活检查，防止僵尸锁残留。

    两层防护：
    1. fcntl.flock (L295): OS 进程退出时自动释放
    2. PID 存活检查 (L312): flock 失败时，检查锁文件中的 PID 是否存活
       → 存活 → 真冲突，拒绝启动
       → 已死 → 僵尸锁，清理后重试
    """

    def __init__(self, agent_id: str):
        self.lock_file = LOCK_DIR / f"aim-client-{agent_id}.lock"
        self.fp: Optional[object] = None

    def acquire(self) -> bool:
        try:
            self.fp = open(self.lock_file, "w")
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            return True
        except (IOError, OSError):
            return self._try_recover_stale()

    def _try_recover_stale(self) -> bool:
        """检查锁文件中 PID 是否存活，清理僵尸锁"""
        try:
            old_pid_str = self.lock_file.read_text().strip()
            old_pid = int(old_pid_str)
            # os.kill(pid, 0) 不发送信号，只检查进程是否存在
            os.kill(old_pid, 0)
            # PID 存活 → 真冲突
            return False
        except (ValueError, OSError):
            # PID 不存在或无效 → 僵尸锁，清理后重试
            self.lock_file.unlink(missing_ok=True)
            return self.acquire()  # 递归重试一次

    def release(self):
        if self.fp:
            try:
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
                self.fp.close()
            except Exception:
                pass
            self.fp = None
            self.lock_file.unlink(missing_ok=True)


# -- 日志 --
def setup_logging(agent_id: str) -> logging.Logger:
    log_dir = Path.home() / ".aim" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"aim-client-{agent_id}.log"
    logger = logging.getLogger("aim-client")
    logger.setLevel(logging.DEBUG)

    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S"))
    # 只保留 FileHandler；StreamHandler 已移除（shell 用 2>&1 捕获 stderr，
    # 同时保留 StreamHandler 会导致每行日志在文件中出现两次）
    logger.addHandler(fh)
    # Debug 输出走 stdout（不双写，shell 不重定向时不丢调试信息）
    dh = logging.StreamHandler(sys.stdout)
    dh.setLevel(logging.DEBUG)
    dh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S"))
    if os.environ.get("AIM_LOG_NO_CONSOLE"):
        dh.setLevel(logging.CRITICAL)  # 静默
    return logger


# -- Identity --
def load_identity(agent_id: str) -> AgentCard:
    identity_path = Path.home() / ".aim" / "agents" / agent_id / "identity.json"
    config_path = Path.home() / ".aim" / "agents" / agent_id / "config.json"
    card = AgentCard()
    if identity_path.exists():
        id_data = json.loads(identity_path.read_text())
        card.global_id = id_data.get("global_id", "")
        card.serial = id_data.get("serial", id_data.get("agent_id", agent_id))
        card.name = id_data.get("name", agent_id)
        exec_model = id_data.get("execution_model", "deferred")
        card.execution_model = exec_model if isinstance(exec_model, str) else exec_model.get("type", "deferred")
    if config_path.exists():
        cfg = json.loads(config_path.read_text())
        card.execution_model = cfg.get("execution_model", card.execution_model)
        # 619-01: 启动前 config schema 校验 (A/B 层)
        try:
            import subprocess as _sp
            _schema_script = str(Path.home() / "shared/aim/aim-client/config_schema_check.py")
            if Path(_schema_script).exists():
                _r = _sp.run([sys.executable, _schema_script, str(config_path)], capture_output=True, timeout=10)
                if _r.returncode == 1:
                    print(f"❌ Config schema A 层校验失败:\n{_r.stderr.decode()}", file=sys.stderr)
                    # 不退出，输出 warning 后继续（给修复机会）
                elif _r.returncode == 0:
                    print(f"✅ [619-01] Config schema v0.2 校验通过")
        except Exception:
            pass  # schema 不可用不阻塞启动
    return card


# -- Transport --
class Transport:
    """Transport 抽象层 -- 基于 SDK AIMNATSClient

    Phase 1: 包装 SDK 客户端
    Phase 2+: 可插拔协议 (HTTP/WS/GRPC)
    """

    def __init__(self, agent_id: str, nats_url: str = "nats://127.0.0.1:4222"):
        from aim_nats_sdk import AIMNATSClient
        self.agent_id = agent_id
        self._logger = logging.getLogger("aim-client.transport")
        creds_path = Path.home() / ".aim" / "agents" / agent_id / "aim.creds"
        self._sdk = AIMNATSClient(
            agent_id=agent_id,
            server=nats_url,
            credentials=str(creds_path) if creds_path.exists() else "",
        )

    @property
    def client(self):
        return self._sdk

    async def connect(self) -> bool:
        try:
            await self._sdk.connect()
            return True
        except Exception as e:
            self._logger.error(f"NATS 连接失败: {e}")
            return False

    async def disconnect(self):
        try:
            await asyncio.wait_for(self._sdk.close(), timeout=5.0)
        except asyncio.TimeoutError:
            self._logger.warning("NATS disconnect timeout, forcing close")

    async def subscribe_dm(self, handler):
        await self._sdk.subscribe_dm(handler)
        self._logger.info(f" 已订阅私聊: aim.dm.{self.agent_id}")

    async def subscribe_grp(self, group_id: str, handler):
        await self._sdk.subscribe_grp(group_id, handler)
        self._logger.info(f" 已订阅群聊: aim.grp.{group_id}")

    async def send_dm(self, to_id: str, text: str):
        """发送私聊消息 + 送达确认"""
        envelope = await self._sdk.send_dm(to_id, text)
        if isinstance(envelope, dict) and "id" in envelope:
            self._my_msg_ids.add(envelope["id"])  # U-007: 追踪发出的消息
            await self.emit_delivery(to_id, envelope["id"], via="dm")
        return envelope

    async def send_grp(self, group_id: str, text: str):
        """发送群聊消息 + 送达确认"""
        result = await self._sdk.send_grp(group_id, text)
        if isinstance(result, dict) and "id" in result:
            self._my_msg_ids.add(result["id"])  # U-007: 追踪发出的消息
            await self.emit_delivery(group_id, result["id"], via="grp")
        return result

    async def authenticate(self) -> bool:
        return True

    async def emit_obs(self, status: str, msg_id: str = "", detail: str = ""):
        """发布 Observer 状态事件（委托给 SDK）"""
        try:
            await self._sdk.emit_obs(status, msg_id, detail)
        except Exception as e:
            self._logger.debug(f"emit_obs 失败: {e}")

    async def emit_health(self, status: str, msg_id: str = "", detail: str = ""):
        """发布健康监控事件到 aim.health.>（healthd 消费，不耗 token）"""
        try:
            import json, time, uuid
            event = {
                "agent_id": self.agent_id,
                "status": status,
                "msg_id": msg_id,
                "detail": detail,
                "ts": time.time(),
                "nonce": uuid.uuid4().hex[:8],
            }
            await self._sdk.nc.publish(f"aim.health.{self.agent_id}", json.dumps(event).encode())
        except Exception as e:
            self._logger.debug(f"emit_health 失败: {e}")

    async def emit_delivery(self, to_id: str, envelope_id: str, via: str = "dm"):
        """推送送达确认事件（observer 可见但不进 dispatch）
        U-006 fix: detail 人可读不丢 JSON，observer 格式干净
        """
        import time
        mid = envelope_id[:8] if len(envelope_id) >= 8 else envelope_id
        # await self._sdk.emit_obs("delivered", mid, f"→ {to_id} via {via}")  # 2026-06-21 禁用以省带宽

    async def send_registry_health_report(self, health: dict):
        return await self.request("aim.registry.health_report", {
            "agent_id": self.agent_id, "health": health,
        }, timeout=3)

    async def send_registry_event(self, event_type: str, detail: dict = None):
        return await self.request("aim.registry.event", {
            "agent_id": self.agent_id, "event_type": event_type, "detail": detail or {},
        }, timeout=3)

    async def query_registry_health(self, agent_id: str) -> dict:
        return await self.request("aim.registry.health_query", {
            "agent_id": agent_id,
        }, timeout=3)

    async def query_registry_events(self, agent_id: str = "", event_type: str = "",
                                     limit: int = 20) -> dict:
        return await self.request("aim.registry.event_query", {
            "agent_id": agent_id, "event_type": event_type, "limit": limit,
        }, timeout=3)

    async def send_registry_heartbeat(self):
        """向 Registry 发送心跳，更新 last_seen"""
        try:
            if self._sdk.nc and self._sdk.nc.is_connected:
                import json as _json, time as _time
                payload = _json.dumps({
                    "agent_id": self.agent_id,
                    "ts": _time.time(),
                }).encode()
                await self._sdk.nc.publish("aim.registry.heartbeat", payload)
        except Exception as e:
            self._logger.debug(f"Registry 心跳失败: {e}")

    async def verify_peer(self, peer_id: str) -> bool:
        cfg = load_global_config()
        peers = cfg.get("trusted_peers", ["ZS0001"])
        return peer_id in peers

    async def request(self, subject: str, payload: dict, timeout: float = 5.0) -> dict:
        """Transport.request() — NATS request-reply 统一入口

        Phase 1 协议要求 7 方法之一。封装 SDK 的 request-reply，
        所有 Registry 交互 (register/health/event/query) 通过此方法。
        失败时返回 {"status": "error", "detail": str(e)}，调用方不需 try/except。
        """
        import json as _json
        try:
            if not self._sdk.nc or not self._sdk.nc.is_connected:
                return {"status": "error", "detail": "NATS not connected"}
            data = _json.dumps(payload).encode()
            resp = await self._sdk.nc.request(subject, data, timeout=timeout)
            return _json.loads(resp.data)
        except Exception as e:
            self._logger.debug(f"request({subject}) failed: {e}")
            return {"status": "error", "detail": str(e)}


# -- AIMClient 主类 --
class AIMClient:
    """AIM Client 主进程 -- Phase 1"""

    def __init__(self, config_path: str):
        self.config_path = config_path = Path(config_path).expanduser()
        self.config = json.loads(config_path.read_text())
        self.agent_id = self.config["agent_id"]
        global_cfg = load_global_config()
        self._default_group = global_cfg.get("default_group", "grp_trio")
        self.logger = setup_logging(self.agent_id)
        self.lock = SingleInstance(self.agent_id)

        # Identity
        self.card = load_identity(self.agent_id)
        self.logger.info(f"Identity: {self.card.serial} ({self.card.name}) execution_model={self.card.execution_model}")

        # Transport
        nats_url = self.config.get("nats_server", "nats://127.0.0.1:4222")
        self.transport = Transport(self.agent_id, nats_url)

        # Security v1
        self.security = SecurityManager(self.config)
        creds_path = Path.home() / ".aim" / "agents" / self.agent_id / "aim.creds"
        self.registry_client = Registry(nats_url, credentials=str(creds_path) if creds_path.exists() else "")

        # Phase 0: Queue + Scheduler + HealthProbe
        self.queue = MessageQueue(capacity=self.config.get("queue_capacity", 1000))
        # 619-14: heartbeat/probe 间隔从 config 统一
        hb_cfg = self.config.get("heartbeat", {})
        probe_interval = hb_cfg.get("interval_ms", 30000) / 1000.0  # ms→s
        self.scheduler = Scheduler(
            processing_timeout=self.config.get("adapter_timeout", 120),
            health_probe_interval=probe_interval,
        )
        adapter_cmd = self.config.get("adapter_cmd", "")
        if adapter_cmd:
            adapter_cmd = str(Path(adapter_cmd).expanduser())
        self.adapter_cmd = adapter_cmd

        # 619-20: 将 config 中 adapter 需要的变量注入子进程环境
        #   adapter.sh 优先级: env → config.json → 硬编码
        #   注入后 adapter 不再依赖 config.json fallback
        # 以父进程环境打底（必须！空 dict 会清空 PATH/HOME 等）
        self.adapter_env: dict[str, str] = dict(os.environ)
        self._degrade_history = deque(maxlen=100)  # P1-2: (ts, exit_code) 窗口
        self._init_loop_state()  # 循环检测追踪
        self._init_fatigue_state()  # U-107: 群聊疲劳检测
        for config_key in ("letta_bin", "letta_agent_id"):
            val = self.config.get(config_key)
            if val:
                self.adapter_env[config_key.upper()] = str(val)

        self.health_probe = HealthProbe(
            health_cmd=f"bash {self.adapter_cmd} health",
            timeout=self.config.get("health_probe_timeout", 25.0),
            env=self.adapter_env,
        )

        self.running = False
        self._dispatch_event = asyncio.Event()
        # 619-18: 群聊回路防护冷却（从 config 读取，默认30s）
        self._grp_cooldown_sec = self.config.get("grp_reply_cooldown_sec", 30.0)
        self._last_grp_reply: dict[str, float] = {}
        self._seen_msg_keys: dict[str, float] = {}  # 内容去重 (from_id:content[:200]→timestamp)
        self._processed_ids: set = set()  # U-005: msg_id L1 去重（接收时）
        self._dispatched_ids: set = set()  # U-006: 已 dispatch 去重（发送 adapter 后）
        self._my_msg_ids: set = set()  # U-007: 本 Agent 发出的消息 ID，用于群聊 reply_to 过滤
        # 619-20: StallWatchdog — 检测 dispatch_loop 假死
        self._last_dispatch_time: float = 0.0
        self._stall_timeout_sec = self.config.get("stall_watchdog_sec", 30.0)
        self._stall_recovery_count: int = 0  # 619-20: 连续自愈计数（>3 次则丢弃卡死消息）
        self._retry_tracker: dict[str, int] = {}  # P0: exit=1 退避, msg_id → retry_count
        self._last_recover_at: float = 0.0  # P0-L3: recover cooldown (60s)
        self._last_trim_at: float = 0.0  # P0-L3: trim cooldown (30s)
        # 620: 自适应 stalled 阈值 (基于 queue depth)
        self._stall_base_sec = self.config.get("stall_watchdog_sec", 30.0)
        self._stall_min_sec = self.config.get("stall_watchdog_min_sec", 10.0)
        self._recover_task: Optional[asyncio.Task] = None  # P2: non-blocking recover handle
        # L3 护栏: N=3 次自修复失败 → 永久停止 + 告警
        self._repair_failures: int = 0
        self._repair_disabled: bool = False
        # 620: envelope 准入校验 (Phase 1: warn, Phase 2: reject)
        self._envelope_strict_mode = self.config.get("envelope_strict_mode", "warn")
        self._envelope_violations: int = 0  # 累计不合规消息数
        self._reject_hard_errors: bool = self._envelope_strict_mode == "reject"
        self.logger.info(f"Framework: {self.config.get('framework', 'unknown')}")
        self.logger.info(f"Adapter: {self.adapter_cmd}")
        self.logger.info(f"Queue+Scheduler 已嵌入 (capacity={self.queue.capacity})")

    def _calc_stall_timeout(self) -> float:
        """620: 自适应 stalled 阈值 — queue 越大超时越短"""
        qsize = self.queue.size()
        qcap = max(self.queue.capacity, 1)
        ratio = min(qsize / qcap, 1.0)
        # 线性缩放: 空队列→base, 满队列→max(base*0.5, min)
        dynamic = max(self._stall_min_sec, self._stall_base_sec * (1.0 - ratio * 0.5))
        return dynamic

    async def _dispatch_loop(self):
        """独立消息投递：Event驱动 + scheduler控制 + StallWatchdog 自愈"""
        while self.running:
            try:
                # 619-20/620: StallWatchdog — 自适应超时替代永久等待
                stall_timeout = self._calc_stall_timeout()
                try:
                    await asyncio.wait_for(self._dispatch_event.wait(), timeout=stall_timeout)
                except asyncio.TimeoutError:
                    pass
                self._dispatch_event.clear()

                # 619-20: 假死自检 — queue 有货但超时无投递 → 强制解锁 scheduler
                import time as _t619_wd
                now_wd = _t619_wd.time()
                # 620: 使用计算后的自适应阈值
                stall_timeout_check = max(self._stall_timeout_sec, stall_timeout)
                if (self.queue.size() > 0 and
                    (self._last_dispatch_time == 0 or now_wd - self._last_dispatch_time > stall_timeout_check)):
                    if self.queue.size() > 0:
                        self._stall_recovery_count += 1
                        self.logger.warning(
                            f"⚠️ StallWatchdog: {self._stall_timeout_sec}s 无投递, queue={self.queue.size()}, 触发自愈 (#{self._stall_recovery_count})"
                        )
                        if self._stall_recovery_count >= 3:
                            # 连续 3 次自愈失败 → 丢弃队首消息，非正常消费
                            stuck = self.queue.dequeue()
                            if stuck:
                                self.queue.ack(stuck.msg_id)  # 标记已处理，不再重试
                                self.logger.error(
                                    f"❌ StallWatchdog: 连续 {self._stall_recovery_count} 次自愈失败，丢弃消息 {stuck.msg_id[:8]} from={stuck.from_id}"
                                )
                            self._stall_recovery_count = 0
                        else:
                            self.scheduler.reset_to_idle()
                        # 620: 修复 StallWatchdog 触发后 _dispatch_event 未 set 致 dispatch 永久阻塞
                        self._dispatch_event.set()
                        self._last_dispatch_time = now_wd  # 只触发一次

                while self.scheduler.should_dispatch() and self.queue.size() > 0:
                    msg = self.queue.dequeue()
                    if not msg:
                        break
                    # U-006: 出队去重用独立 _dispatched_ids，不与接收时 L1 _processed_ids 冲突
                    if msg.msg_id and msg.msg_id in self._dispatched_ids:
                        self.logger.debug(f" [DEDUP DEQUEUE] msg_id={msg.msg_id[:8]} 已 dispatch, ack跳过")
                        self.queue.ack(msg.msg_id)
                        continue
                    self._last_dispatch_time = _t619_wd.time()  # 记录最近投递时间
                    self.scheduler.on_dispatch_started()
                    self.logger.info(f"投递: {msg.msg_id[:8]} from={msg.from_id}")
                    try:
                        await self.transport.send_ack(msg.from_id, msg.msg_id)
                    except Exception:
                        pass
                    try:
                        # ── 免 LLM 消息：系统通知和确认消息不烧 token ──
                        if self._skip_adapter_for_operational(msg):
                            self.logger.debug(f" [{msg.msg_id[:8]}] 免LLM跳过: from={msg.from_id}")
                            self.queue.ack(msg.msg_id)
                            continue
                        # U-107: 群聊 mute 检测 — 疲劳期不发 adapter
                        if msg.grp_id and self._is_group_muted(msg.grp_id):
                            self.logger.debug(f" [{msg.msg_id[:8]}] 🔇 群聊 mute 跳过: {msg.grp_id}")
                            self._dispatch_event.set()  # 不阻塞后续消息
                            self.scheduler.on_processing_done()
                            self.queue.ack(msg.msg_id)
                            continue
                        reply = await self._call_adapter(msg)
                        if reply:
                            if msg.grp_id:
                                # 619-18: 群聊回路防护（30s 冷却，防回复风暴）
                                import time as _t619
                                now = _t619.time()
                                last = self._last_grp_reply.get(msg.grp_id, 0)
                                if now - last < self._grp_cooldown_sec:
                                    self.logger.debug(f" [{msg.msg_id[:8]}] 群聊回复跳过（冷却 {now-last:.0f}s/{self._grp_cooldown_sec}s）")
                                elif self._is_confirm_loop(msg, reply):
                                    self.logger.info(f" [{msg.msg_id[:8]}] 群聊确认循环跳过: in={msg.text[:20]} out={reply[:20]}")
                                else:
                                    self._last_grp_reply[msg.grp_id] = now
                                    await self.transport.send_grp(msg.grp_id, reply)
                            else:
                                await self.transport.send_dm(msg.from_id, reply)
                        self.scheduler.on_processing_done()
                        # U-006: 标记已 dispatch，防止回队重复处理
                        if msg.msg_id:
                            self._dispatched_ids.add(msg.msg_id)
                        self.queue.ack(msg.msg_id)
                        self._stall_recovery_count = 0  # 620-01: 成功投递才清零
                        self._retry_tracker.pop(msg.msg_id, None)  # 退避跟踪清除
                    except DegradeError:
                        self.scheduler.on_degrade()
                        self.queue.nack(msg.msg_id, "degrade")
                        # 619-11: 降级告警
                        self.logger.warning(f"⚠️  [{self.agent_id}] adapter 降级，停止投递")
                        await self.transport.emit_health("degrade", msg.msg_id[:8], "adapter DEGRADE")
                        # FIX(2026-06-19): break 后必须重置事件，否则永久阻塞（火鸡儿发现）
                        self._dispatch_event.set()
                        break

                    except RetryableError:
                        self.scheduler.on_retry()
                        rt = self._retry_tracker.get(msg.msg_id, 0) + 1
                        self._retry_tracker[msg.msg_id] = rt
                        if rt >= 3:
                            self.logger.warning(f" [{msg.msg_id[:8]}] 退避耗尽 ({rt}次)，入死信")
                            self.queue.ack(msg.msg_id)  # ack 移除，不 requeue
                            if msg.msg_id:
                                self._dispatched_ids.add(msg.msg_id)
                            self._retry_tracker.pop(msg.msg_id, None)
                            self.scheduler.reset_to_idle()
                            self._dispatch_event.set()  # 立即触发下一轮
                            self._stall_recovery_count = 0  # 成功 discard 后重置计数
                            # P0-L3: backoff exhausted -> trim stuck session
                            await self._call_adapter_trim()
                        else:
                            delay = [2, 4, 8][rt - 1]
                            self.logger.info(f" [{msg.msg_id[:8]}] 退避 {rt}/3, delay={delay}s")
                            self.queue.nack(msg.msg_id, "retry")
                            await asyncio.sleep(delay)

                    except Exception as e:
                        if "agent_unreachable" in str(e):
                            self.scheduler.on_degrade()
                            self.queue.nack(msg.msg_id, "agent_unreachable")
                            self.logger.warning(f"🔌 [{self.agent_id}] Agent 不可达（exit=4），暂停投递")
                            await self.transport.emit_health("agent_unreachable", msg.msg_id[:8], "exit=4")
                            self._dispatch_event.set()
                            # P2: non-blocking recover
                            if self._recover_task and not self._recover_task.done():
                                self.logger.debug(f"[{self.agent_id}] recover already in progress, skipping")
                            else:
                                self._recover_task = asyncio.create_task(self._call_adapter_recover())
                            break
                        raise
                    except HumanInterventionError:
                        self.scheduler.on_human_intervention()
                        self.queue.nack(msg.msg_id, "human_intervention")
                        self.logger.error(f"💀 [{self.agent_id}] FATAL exit=3, 永久停止 dispatch")
                        await self.transport.emit_health("fatal", msg.msg_id[:8], "exit=3")
                        self._dispatch_event.set()
                        break
                    except Exception as e:
                        self.logger.error(f"投递异常 [{msg.msg_id[:8]}]: {e}")
            except Exception as e:
                self.logger.error(f"投递循环异常: {e}")
                await asyncio.sleep(5)
            # 619-09: SIGHUP 重载检查（每轮循环检测）
            if self._reload_flag:
                self._reload_flag = False
                self._reload_config()

    async def start(self):
        if not self.lock.acquire():
            self.logger.error("另一个 aim-client 已在运行")
            sys.exit(1)

        atexit.register(self.lock.release)
        signal.signal(signal.SIGTERM, lambda *_: self._shutdown())
        signal.signal(signal.SIGALRM, lambda *_: os._exit(0))  # P2: drain timeout 兜底
        self._reload_flag = False
        signal.signal(signal.SIGHUP, lambda *_: setattr(self, '_reload_flag', True))  # 619-09

        # NATS 连接重试（最多3次）
        for attempt in range(3):
            if await self.transport.connect():
                break
            self.logger.warning(f"NATS 连接失败 (attempt {attempt+1}/3)")
            await asyncio.sleep(3)
        else:
            self.logger.error("NATS 连接失败，退出")
            sys.exit(1)

        await self.transport.authenticate()

        # 自动向 Registry 注册
        await self._register_with_registry()

        self.logger.info("等待 NATS 稳定 (5s)...")
        await asyncio.sleep(5)

        # 订阅
        await self.transport.subscribe_dm(self._on_dm)
        cfg = load_global_config()
        default_grp = cfg.get("default_group", "grp_trio")
        for gid in default_grp.split(","):
            await self.transport.subscribe_grp(gid.strip(), self._on_grp)

        self.running = True
        self.logger.info(f" {self.agent_id} AIM Client v{_AIM_VERSION} 启动完成")

        # NOTICE 1.3.0: 运行时版本检查（拒绝低于最低要求的 SDK）
        _MIN_SDK = "1.3.0"
        def _ver_tuple(v: str):
            return tuple(int(x) for x in v.strip().split("."))
        try:
            from packaging.version import Version
            _ver_ok = Version(_AIM_VERSION) >= Version(_MIN_SDK)
        except ImportError:
            _ver_ok = _ver_tuple(_AIM_VERSION) >= _ver_tuple(_MIN_SDK)
        if not _ver_ok:
            self.logger.error(f"SDK version {_AIM_VERSION} < {_MIN_SDK}，启动中止")
            self.running = False
            return

        # 初始化持久化队列（恢复未 ack 消息）
        # 按 agent_id 分文件，避免三方 Agent 互踩（v1.3.0 bug 修复 2026-06-19）
        persist_path = self.config.get("queue_persist_path")
        if persist_path:
            persist_path = str(Path(persist_path).expanduser())
        else:
            persist_path = str(Path.home() / ".aim" / "agents" / self.agent_id / "queue.jsonl")
        await self.queue.init_persist(persist_path)
        self.logger.info(f"Queue 持久化: {persist_path}")

        # 健康探针循环
        asyncio.create_task(self._health_probe_loop())
        asyncio.create_task(self._dispatch_loop())
        self._dispatch_event.set()

        # 主循环保持
        while self.running:
            await asyncio.sleep(5)

    async def _health_probe_loop(self):
        """Phase 0: 健康探针 -> 更新 Scheduler -> 触发投递"""
        _last_state = "OK"  # 619-11: 状态变迁追踪
        while self.running:
            try:
                report = await self.health_probe.probe()
                prev_can = self.scheduler.should_dispatch()
                self.scheduler.update_state(report)
                # 619-11: 状态变迁检测 + 告警
                new_state = report.status.name if hasattr(report, 'status') else str(report.status)
                if new_state != _last_state and new_state in ("BUSY", "DEGRADE", "OFFLINE"):
                    self.logger.warning(f"⚠️  [{self.agent_id}] adapter {_last_state}→{new_state}")
                    await self.transport.emit_health("state_change", "", f"{_last_state}→{new_state}")

                _last_state = new_state
                if not prev_can and self.scheduler.should_dispatch():
                    self._dispatch_event.set()
                # ── 0-ack 告警（项6）：队列堆积但无处理进展 → observer 事件 ──
                qsize = self.queue.size()
                prev_qsize = getattr(self, '_prev_queue_size', 0)
                self._prev_queue_size = qsize
                # 队列有货 且 数量未减少（无 ack 消化）
                if qsize > 0 and qsize >= prev_qsize:
                    zs = getattr(self, '_zero_ack_streak', 0) + 1
                    self._zero_ack_streak = zs
                else:
                    self._zero_ack_streak = 0
                if self._zero_ack_streak >= 3 and qsize > 0:
                    self.logger.warning(f"⚠️ 0-ack: queue={qsize} pending, {self._zero_ack_streak} 周期未消化")
                    now = time.time()
                    last_emit = getattr(self, '_last_0ack_emit', 0)
                    if now - last_emit > 300:
                        self._last_0ack_emit = now
                        await self.transport.emit_health("0-ack", "", f"queue={qsize} streak={self._zero_ack_streak}")

                # Registry 心跳：更新 last_seen
                await self.transport.send_registry_heartbeat()
                # 健康心跳（推送到 healthd，不耗 token）
                await self.transport.emit_health("heartbeat", "", "alive")
                # P1: 上报健康快照到 Registry KV
                h = {
                    'adapter_ok': report.status.name != 'OFFLINE',
                    'status': report.status.name,
                }
                if hasattr(self, 'queue'):
                    h['queue_size'] = self.queue.size()
                await self.transport.send_registry_health_report(h)
            except Exception as e:
                self.logger.error(f"健康探针异常: {e}")
            interval = self.scheduler.get_probe_interval()
            await asyncio.sleep(interval)


    # P1-2: 滑动窗口 DEGRADE 检查
    def _should_degrade(self, exit_code: int) -> bool:
        """30s 内 >=2 次 exit=2 → True，否则只记录不触发"""
        now = time.time()
        self._degrade_history.append((now, exit_code))
        while self._degrade_history and self._degrade_history[0][0] < now - DEGRADE_WINDOW_S:
            self._degrade_history.popleft()
        count = sum(1 for _, ec in self._degrade_history if ec == 2)
        return count >= DEGRADE_WINDOW_COUNT

    # ── 群聊无效沟通检测 ──────────────────────────
    # 原则：不看字数/关键词，看信息增量。
    # 无效沟通 = 无新信息 + 无决策/行动 + 无状态变化
    # 三层判定：L0 礼貌剥离 → L1 内容新意 → L2 连续计数器
    GRP_FATIGUE_WINDOW = 300      # 追踪窗口（秒），窗口外自动复位
    GRP_FATIGUE_MAX_EMPTY = 3     # 连续 N 轮无效 → 触发 mute
    GRP_FATIGUE_MUTE = 60         # mute 时长（秒）

    # L0 礼貌用语剥离表
    _POLITENESS_STRIP = [
        # 前缀：确认/收到类
        r'^(收到|收到了|好的|明白|明白了|了解|了解了|知道|知道了|确认|已收到|已确认|已了解|已阅|收到|OK|ok|Ok)\s*',
        r'^(收到|看到了)\s*[\u4e00-\u9fff]{1,6}的\s*(反馈|消息|回复|通知|建议|方案|分析|总结|意见|进度|报告|代码)\s*',
        # 前缀：赞同类
        r'^[\u4e00-\u9fff]{1,6}的\s*(方案|思路|方向|做法|设计|代码|修复)\s*(很好|不错|可以|没问题)\s*',
        # 后缀：客气话
        r'\s*(辛苦了|谢谢|感谢|多谢|没问题|继续保持|随时联系|随时沟通|一起加油|共同努力|我们继续|继续保持|有进展再同步)\s*$',
        r'\s*[，,][，,\s]*(看起来没问题|感觉没问题|应该没问题|没毛病|可以|行的|👌|✅|👍)\s*$',
        # 后缀：展望类废话
        r'\s*[，,]\s*(我们继续|继续保持|再接再厉|稳步推进|有序推进|按计划推进|照计划执行|后续跟进|有问题再沟通)\s*$',
    ]

    # 技术/行动关键词：出现这些词的消息必定有效
    # 注意：不含常见于客套语中的词（adapter/修复/方案/设计/架构/Queue/L1/P0等）
    # 这些词让信息密度检查 + 连续计数器处理
    _SUBSTANCE_MARKERS = [
        "http", "P0-", "U-", "T0", "msg_id", "pid", "exit=",
        "代码", "config", ".py", ".sh", ".md", "shared/", "~/", "NATS",
        "修改", "部署", "测试", "重启", "联调", "上线", "发布", "推送",
        "BUG", "bug", "报错", "日志", "log", "错误", "error",
        "通知", "提交", "commit", "git", "commit:",
        "版本", "version", "VERSION", "CHANGELOG",
        "`", "→", "⚠", "🔴", "🟡", "🟢",
    ]

    # 问题/请求标记：带问号或请求语气的消息有效
    _REQUEST_MARKERS = ["？", "?", "请", "帮我", "需要", "麻烦", "能否"]

    # 确认类关键词集
    _ACK_CORE_WORDS = {"收到", "ok", "OK", "Ok", "好的", "明白", "了解", "知道", "1", "确认"}

    # U-006: 信号/测试消息关键词 — 不调 adapter，零 token 消耗
    SIGNAL_PATTERNS = {"TEST-", "TEST_", "LOG-FIX-", "INVOKE-", "STACKTRACE-", "LOCK-TEST", "DEDUP-", "PING", "通信正常", "通道打通", "回执确认", "通道确认", "PONG"}
    # 系统发送者：消息不应经过 LLM
    SYSTEM_SENDERS_SET = {"alertd", "registry", "aim-watch", "observer"}
    # 社交结束语：纯礼貌用语，不需 LLM 处理
    SOCIAL_CLOSE = {"晚安", "再见", "拜拜", "明天见", "辛苦", "好梦", "早点休息", "养足精神"}

    def _skip_adapter_for_operational(self, msg) -> bool:
        """免 LLM：系统通知/确认消息/测试信号不调 adapter，零 token 消耗"""
        if msg.from_id in self.SYSTEM_SENDERS_SET:
            return True
        text = (msg.content or "").strip()
        if not text:
            return True
        # 信号/测试消息（零 token）
        if any(p in text for p in self.SIGNAL_PATTERNS):
            return True
        # 社交结束语（短 + 含结束词）
        if len(text) <= 50 and any(w in text for w in self.SOCIAL_CLOSE):
            return True
        # 短消息 + 纯确认 → 免 LLM（≤8字，交给 _has_substance 判定）
        if len(text) <= 8 and not self._has_substance(text):
            return True
        return False

    def _init_fatigue_state(self):
        """U-107: 初始化群聊疲劳检测状态"""
        import collections
        self._grp_fatigue: dict = collections.defaultdict(list)  # grp_id → [(ts, is_effective), ...]
        self._group_muted_until: dict[str, float] = {}  # grp_id → mute_expiry_ts
        self._ineffective_rounds: dict[str, int] = collections.defaultdict(int)  # grp_id → 连续无效轮数
        self._grp_recent_texts: dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=5)  # 最近 5 条群聊消息（用于内容新意检查）
        )

    def _strip_politeness(self, text: str) -> tuple[str, float]:
        """L0: 剥离礼貌用语，返回 (核心内容, 剥离率)
        
        剥离率 = 被移除字符数 / 原始长度。>0.5 表示大部分是客套。
        """
        import re as _re
        t = text.strip()
        orig_len = len(t)
        if orig_len == 0:
            return "", 1.0
        # 移除 emoji 前缀装饰
        t = _re.sub(r'^[🐸🐴🐤✨👂🤝🦊🤖📋📊📡🛡️🔧⚙️🎯💡\s]+', '', t)
        # 逐条应用剥离规则
        for pat in self._POLITENESS_STRIP:
            t = _re.sub(pat, '', t).strip()
        stripped_len = len(t)
        ratio = (orig_len - stripped_len) / orig_len if orig_len > 0 else 1.0
        return t, ratio

    def _has_substance(self, text: str) -> bool:
        """判定消息是否有实质内容。
        
        原则（大哥 2026-06-21）：不看字数，看信息增量。
        默认无效 — 只有包含具体信息的才算有效。
        30字 "收到你的反馈，我们继续推进" = 无效。
        15字 "P0-005 死锁，exit=2" = 有效。
        """
        import re as _re
        t = text.strip()
        if not t:
            return False

        # ── 正向信号：包含任一则有效 ──
        # 1) 具体技术标记（ID/路径/代码/版本）
        for marker in self._SUBSTANCE_MARKERS:
            if marker in t:
                return True
        # 2) 含数字（15项、3轮、v1.3）
        if _re.search(r'\d+', t):
            return True
        # 3) 含问句/请求
        for marker in self._REQUEST_MARKERS:
            if marker in t:
                return True
        if _re.search(r'[？?]', t):
            return True
        # 4) 含决策/行动动词（在具体语境中）
        if _re.search(r'(采用|确定|选择|改为|按照|决定|分配|认领|负责|指派|修改|部署|重启|提交|推送|联调|上线|发布)', t):
            return True
        # 5) 含完成/状态变化
        if _re.search(r'(完成|✅|通过|交付|验证|修了|修好|改好|调通|OK|OK了|好了|搞定了)', t):
            return True
        # 6) 含错误/异常
        if _re.search(r'(BUG|bug|error|Error|报错|错误|异常|失败|超时|死锁|卡住|挂了)', t):
            return True

        # ── 负向信号：明确无效的模式 ──
        # 短确认词
        if len(t) <= 4:
            stripped = t.rstrip('，,。.!！?？✅👍👌✨，。')
            if stripped in self._ACK_CORE_WORDS:
                return False

        # 剥离礼貌用语
        core, ratio = self._strip_politeness(t)

        # 剥离率很高的 → 几乎全是客套
        if ratio > 0.5 and len(core) < 20:
            return False

        # 剥离后是纯确认词
        if core in self._ACK_CORE_WORDS:
            return False
        if len(core) <= 6 and any(w in core for w in self._ACK_CORE_WORDS if len(w) > 1):
            return False

        # 剥离后纯标点/空白 → 无效
        core_no_punct = _re.sub(r'[\s，,。.!！?？、：:；;…\.\-－—─~～·•]', '', core)
        if len(core_no_punct) < 4:
            return False

        # 长消息但无上述任何具体信息 → 很可能是客套（AI 之间的礼貌循环）
        # 默认无效：AI 之间没有具体信息的交流就是无效沟通
        return False

    def _content_novelty(self, grp_id: str, text: str) -> float:
        """L1: 内容新意检查。返回 0.0~1.0，值越高越有新意。
        
        与群聊最近消息做 trigram 相似度对比。
        < 0.3 = 高度重复（回声/鹦鹉），>0.6 = 有新意。
        """
        import re as _re
        t = _re.sub(r'[🐸🐴🐤✨👂🤝\s]', '', text.strip())
        if len(t) < 6:
            return 0.5  # 太短无法判断，中性
        recent = list(self._grp_recent_texts.get(grp_id, []))
        if not recent:
            return 1.0  # 没有历史，视为新
        # trigram 集合
        def _trigrams(s):
            return {s[i:i+3] for i in range(len(s)-2)}
        t_tri = _trigrams(t)
        if not t_tri:
            return 1.0
        max_sim = 0.0
        for past in recent:
            p_tri = _trigrams(_re.sub(r'[🐸🐴🐤✨👂🤝\s]', '', past.strip()))
            if not p_tri:
                continue
            overlap = len(t_tri & p_tri)
            union = len(t_tri | p_tri)
            sim = overlap / union if union > 0 else 0.0
            if sim > max_sim:
                max_sim = sim
        return 1.0 - max_sim  # 新意 = 1 - 最大相似度

    def _is_ineffective(self, grp_id: str, text: str) -> bool:
        """群聊消息无效判定
        
        原则：不看字数/关键词，看信息增量。
        _has_substance 是唯一权威判定。新意检查仅用于确认回声。
        """
        if not text or not text.strip():
            return True
        # 主判定：_has_substance
        if self._has_substance(text):
            return False  # 有实质 → 有效
        # 无实质 → 无效（新意检查仅用于日志，不覆盖判定）
        # 例外：极短确认（≤4字）不计数 — 这些走 _skip_adapter_for_operational
        if len(text.strip()) <= 4:
            return False  # 太短不计数，避免 "收到" 触发疲劳
        self.logger.debug(f" [INEFFECTIVE] 无实质内容: {text[:60]}")
        return True

    def _record_grp_msg(self, grp_id: str, text: str):
        """记录群聊消息（用于内容新意对比）"""
        self._grp_recent_texts[grp_id].append(text.strip())

    def _record_grp_reply(self, group_id: str, reply: str, is_effective: bool):
        """U-107: 记录群聊回复，追踪连续无效轮数"""
        now = time.time()
        self._grp_fatigue[group_id].append((now, is_effective))
        # 清理过期记录
        cutoff = now - self.GRP_FATIGUE_WINDOW
        self._grp_fatigue[group_id] = [
            (ts, eff) for ts, eff in self._grp_fatigue[group_id] if ts > cutoff
        ]
        if len(self._grp_fatigue[group_id]) > 50:
            self._grp_fatigue[group_id] = self._grp_fatigue[group_id][-30:]
        # 更新连续无效计数
        if not is_effective:
            self._ineffective_rounds[group_id] = self._ineffective_rounds.get(group_id, 0) + 1
        else:
            self._ineffective_rounds[group_id] = 0  # 有效回复 → 归零

    def _grp_is_fatigued(self, group_id: str) -> bool:
        """U-107: 群聊是否已进入无效沟通循环 → 应 mute"""
        rounds = self._ineffective_rounds.get(group_id, 0)
        if rounds < self.GRP_FATIGUE_MAX_EMPTY:
            return False
        # 检查 mute 冷却
        now = time.time()
        muted_until = self._group_muted_until.get(group_id, 0)
        if now < muted_until:
            return True  # 仍在 mute
        # 触发 mute
        self._group_muted_until[group_id] = now + self.GRP_FATIGUE_MUTE
        self._ineffective_rounds[group_id] = 0
        self.logger.warning(
            f"⛔ 群聊无效沟通 mute: {group_id} "
            f"(连续 {rounds} 轮无效 → mute {self.GRP_FATIGUE_MUTE}s, 到 {now + self.GRP_FATIGUE_MUTE:.0f})"
        )
        return True

    def _is_group_muted(self, group_id: str) -> bool:
        """检查群聊是否在 mute 中"""
        muted_until = self._group_muted_until.get(group_id, 0)
        if time.time() < muted_until:
            return True
        # mute 过期自动清理
        if muted_until > 0:
            del self._group_muted_until[group_id]
            self.logger.info(f"🔇 群聊 mute 到期: {group_id}")
        return False

    def _is_confirm_loop(self, msg, reply: str) -> bool:
        """检测群聊确认死锁：疲劳检测 → mute 60s
        
        新原则（2026-06-21 大哥）：不看字数/关键词，看信息增量。
        30-100字的「收到+客气话」同样判定为无效。
        """
        if not msg.grp_id:
            return False
        in_text = (msg.content or "").strip()
        out_text = reply.strip()
        # 入站消息记录到内容历史
        self._record_grp_msg(msg.grp_id, in_text)
        # 出站回复判定
        is_effective = not self._is_ineffective(msg.grp_id, out_text)
        self._record_grp_reply(msg.grp_id, out_text, is_effective)
        if self._grp_is_fatigued(msg.grp_id):
            self.logger.info(
                f" [{msg.msg_id[:8]}] ⛔ 群聊疲劳 mute: {msg.grp_id} "
                f"(连续 {self.GRP_FATIGUE_MAX_EMPTY} 轮无效, mute {self.GRP_FATIGUE_MUTE}s)"
            )
            return True
        return False

    async def _call_adapter_recover(self) -> bool:
        """Call adapter.sh recover, returns True if backend recovered.

        exit=0 + JSON -> success
        exit!=0 -> failure, log stderr
        60s cooldown to prevent recover storms
        L3 护栏: >=3 次连续失败 → agent_stalled 告警 + 永久停止自修复
        """
        if self._repair_disabled:
            return False

        _now = __import__("time").time()
        if _now - self._last_recover_at < 60:
            self.logger.debug(f"recover cooldown ({_now - self._last_recover_at:.0f}s/60s)")
            return False
        self._last_recover_at = _now

        self.logger.info("[recover] Calling adapter recover ...")
        cmd = f"bash {self.adapter_cmd} recover"
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=self.adapter_env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60.0  # L3: adapter recover 多轮重试（匹配降配后的≈68s）
            )
        except asyncio.TimeoutError:
            proc.kill()
            self.logger.warning("[recover] Timeout (30s)")
            self._record_repair_failure("recover_timeout")
            return False
        except Exception as exc:
            self.logger.warning(f"[recover] Exception: {exc}")
            self._record_repair_failure(f"recover_exception:{exc}")
            return False

        rc = proc.returncode or 0
        stderr_text = stderr.decode().strip() if stderr else ""
        stdout_text = stdout.decode().strip() if stdout else ""

        if rc == 0:
            try:
                info = __import__("json").loads(stdout_text)
            except Exception:
                info = {"raw": stdout_text[:200]}
            self.logger.info(f"[recover] Success: {info}")
            self._repair_failures = 0  # L3: 成功后重置
            # P1: report recover event
            await self.transport.send_registry_event("recover", {"exit_code": rc, "result": info})
            return True
        else:
            self.logger.warning(f"[recover] Failed (exit={rc}): {stderr_text[:120]}")
            self._record_repair_failure(f"recover_exit={rc}")
            # P1: report recover failure
            await self.transport.send_registry_event("recover", {"exit_code": rc, "error": stderr_text[:200]})
            return False

    async def _call_adapter_trim(self) -> None:
        """Call adapter.sh trim, clear stuck session/messages.

        30s cooldown.
        L3 护栏: repair_disabled 时跳过
        """
        if self._repair_disabled:
            return
        _now = __import__("time").time()
        if _now - self._last_trim_at < 30:
            self.logger.debug(f"trim cooldown ({_now - self._last_trim_at:.0f}s/30s)")
            return
        self._last_trim_at = _now

        self.logger.info("[trim] Calling adapter trim ...")
        cmd = f"bash {self.adapter_cmd} trim"
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=self.adapter_env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=15.0
            )
        except asyncio.TimeoutError:
            proc.kill()
            self.logger.warning("[trim] Timeout (15s)")
            return
        except Exception as exc:
            self.logger.warning(f"[trim] Exception: {exc}")
            return

        rc = proc.returncode or 0
        stdout_text = stdout.decode().strip() if stdout else ""
        if rc == 0:
            self.logger.info(f"[trim] Done: {stdout_text[:200]}")
            self._repair_failures = 0  # L3: 成功后重置
        else:
            stderr_text = stderr.decode().strip() if stderr else ""
            self.logger.warning(f"[trim] Failed (exit={rc}): {(stdout_text + stderr_text)[:120]}")
            self._record_repair_failure(f"trim_exit={rc}")
        # P1: report trim event
        import json as _pj1
        try: info = _pj1.loads(stdout_text) if stdout_text else {}
        except Exception: info = {"raw": stdout_text[:200] if stdout_text else ""}
        await self.transport.send_registry_event("trim", {"exit_code": rc, "result": info})

    def _record_repair_failure(self, reason: str = ""):
        """L3 护栏: 记录自修复失败，N=3 触发 agent_stalled 告警并永久停止"""
        self._repair_failures += 1
        n = self._repair_failures
        self.logger.warning(f"[L3] 自修复失败 #{n}/3: {reason}")
        if n >= 3:
            self._repair_disabled = True
            self.logger.error(
                f"💀 [L3] {self.agent_id} 自修复连续 {n} 次失败，永久停止自修复"
            )
            # 通过 health 通道发布 stalled 告警
            asyncio.ensure_future(
                self.transport.emit_health(
                    "stalled",
                    "",
                    f"自修复连续{n}次失败，已停止。最后原因: {reason}"
                )
            )

    # ── 循环检测（行为模式，非关键词）──
    _LOOP_WINDOW_SEC = 60
    _LOOP_REPEAT_THRESHOLD = 3        # 同一 (from_id, 短内容) 窗口内 ≥3 次 → 视为循环
    _LOOP_CONTENT_MAX_LEN = 20        # 只有短消息参与循环检测

    def _init_loop_state(self):
        """初始化循环追踪状态（在 __init__ 中调用）"""
        # {(from_id, content_fingerprint): deque[timestamp]}
        self._loop_tracker: dict = {}

    def _check_loop(self, from_id: str, content: str, msg_id: str) -> bool:
        """检测消息是否处于循环中（行为模式：同发送者+同短内容高频重复）

        大哥原则：不看固定词汇，看行为——同一来源反复发同样的短消息。
        真正的对话（如"收到，确认我的归属项：…"）不会重复，不会被误杀。
        """
        import time as _time
        now = _time.time()
        text = content.strip()

        # 只追踪短消息（长消息几乎不可能进入循环）
        if len(text) > self._LOOP_CONTENT_MAX_LEN:
            return False

        # 指纹：from_id + 内容前 20 字
        key = (from_id, text[:20])
        dq = self._loop_tracker.get(key)
        if dq is None:
            dq = __import__("collections").deque(maxlen=50)
            self._loop_tracker[key] = dq

        # 清理过期
        cutoff = now - self._LOOP_WINDOW_SEC
        while dq and dq[0] < cutoff:
            dq.popleft()

        dq.append(now)
        count = len(dq)

        if count >= self._LOOP_REPEAT_THRESHOLD:
            self.logger.warning(
                f" [{msg_id[:8]}] 🔁 循环检测: from={from_id} cnt={count}/{self._LOOP_WINDOW_SEC}s "
                f"content={text[:30]!r}"
            )
            return True
        return False

    async def _call_adapter(self, msg: Message) -> Optional[str]:
        """调用 adapter.sh process，返回回复文本

        Returns:
            str: AI 回复文本（可能为空）
            None: 回复为空（静默）

        Raises:
            RetryableError: exit=1
            DegradeError: exit=2
            HumanInterventionError: exit=3
        """
        # ── 循环抑制：同来源同内容高频重复 → 静默跳过 ──
        if self._check_loop(msg.from_id, msg.content, msg.msg_id):
            return None

        safe_content = msg.content.replace("'", "'\\''")
        cmd = f"bash {self.adapter_cmd} process --from '{msg.from_id}' --message '{safe_content}'"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=self.adapter_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.scheduler.processing_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            self.logger.warning(f" [{msg.msg_id[:8]}] adapter 超时 ({self.scheduler.processing_timeout}s)")
            raise RetryableError("adapter 超时")

        rc = proc.returncode or 0
        stderr_text = stderr.decode().strip() if stderr else ""
        stdout_text = stdout.decode().strip() if stdout else ""

        if stderr_text:
            self.logger.debug(f" [{msg.msg_id[:8]}] adapter stderr: {stderr_text[:100]}")

        if rc == 0:
            # 正常回复
            if stdout_text:
                self.logger.debug(f" [{msg.msg_id[:8]}] adapter OK, reply={stdout_text[:60]}")
            else:
                self.logger.debug(f" [{msg.msg_id[:8]}] adapter OK, 空回复")
            return stdout_text or None

        elif rc == 1:
            # 可重试（exit=1）：session 忙、排队中等
            raise RetryableError(stderr_text or f"adapter exit=1")

        elif rc == 2:
            # P1-2: 滑动窗口检查，30s内>=2次exit=2才DEGRADE
            if self._should_degrade(2):
                raise DegradeError(stderr_text or f"adapter exit=2")
            # 窗口未达标 → nack 重试
            self.logger.info(f"exit=2 滑动窗口未达标，nack 重试")
            raise RetryableError(stderr_text or "adapter exit=2, under window")
        elif rc == 3:
            # FATAL（exit=3）：配置错误、CLI不存在、环境问题 → 永久停止
            raise HumanInterventionError(stderr_text or f"adapter exit=3")
        elif rc == 4:
            # AGENT_UNREACHABLE（exit=4）：agent数据不在磁盘/框架崩溃 → DEGRADE+可恢复
            raise DegradeError(f"[agent_unreachable] {stderr_text}" if stderr_text else "agent unreachable")
        else:
            # 5+ UNKNOWN → 按 FATAL 处理（未知即不安全）
            raise HumanInterventionError(f"unknown exit={rc}: {stderr_text[:100]}")

    # -- NATS 回调 (SDK 签名: handler(envelope_dict, raw_msg)) --

    @staticmethod
    def _preview(envelope: dict, maxlen: int = 50) -> str:
        """提取消息内容截断预览"""
        text = envelope.get("payload", {}).get("text", "")
        if not text:
            return ""
        return text[:maxlen] + ("…" if len(text) > maxlen else "")

    async def _on_dm(self, envelope: dict, raw_msg):
        from_id = envelope.get("from", envelope.get("from_id", ""))
        mid = str(envelope.get('id','?'))[:8]
        preview = self._preview(envelope, maxlen=50)
        obs_text = self._preview(envelope, maxlen=500)
        self.logger.info(f" DM收到: from={from_id} id={mid}")
        if from_id == self.agent_id:
            return
        detail = f"from={from_id}" + (f" text={obs_text}" if obs_text else "")
        # await self.transport.emit_obs("received", mid, detail)  # 2026-06-21 禁用以省带宽
        await self._handle_message(envelope, is_dm=True)

    async def _on_grp(self, envelope: dict, raw_msg):
        from_id = envelope.get("from", envelope.get("from_id", ""))
        mid = str(envelope.get('id','?'))[:8]
        preview = self._preview(envelope, maxlen=50)
        obs_text = self._preview(envelope, maxlen=500)
        self.logger.info(f" GRP收到: from={from_id}")
        if from_id == self.agent_id:
            return
        detail = f"from={from_id}" + (f" text={obs_text}" if obs_text else "")
        # await self.transport.emit_obs("received", mid, detail)  # 2026-06-21 禁用以省带宽
        await self._handle_message(envelope, is_dm=False)

    async def _handle_message(self, envelope: dict, *, is_dm: bool):
        # ── 620: envelope 准入校验 (三方共识: 吉量SDK + 呱呱handler + 火鸡儿E2E) ──
        violation = self._validate_envelope(envelope)
        if violation:
            self._envelope_violations += 1
            sev, reason = violation
            detail = f"{sev} envelope from={envelope.get('from','?')} id={(envelope.get('id','?') or '?')[:8]}: {reason}"
            if sev == "hard" and self._reject_hard_errors:
                self.logger.warning(f"🚫 [envelope] REJECT {detail}")
                return
            else:
                self.logger.warning(f"⚠️ [envelope] WARN {detail} (violations={self._envelope_violations})")

        payload = envelope.get("payload", {})
        content = payload.get("text", "")
        # 620: 移除 envelope.get("content") 容错回退，不合规格式已在上面告警
        if not content:
            return

        # ── 送达确认替代 ACK：短 ACK 不进 dispatch ──
        # 传输层 NATS publish ack 已保证送达，应用层 ACK 是冗余
        import re
        stripped = content.strip()
        # 去除 emoji/符号后提取纯文本
        _text_only = re.sub(r'[^\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', '', stripped).strip()
        # 压缩空白后判断
        _compact = re.sub(r'\s+', '', _text_only)
        _ACK_PATTERNS = {"收到", "ok", "OK", "Ok", "done", "Done", "ack", "ACK", "Ack"}
        _ACK_REPEATS = {"收到收到", "okok", "donedone", "ackack"}
        if _text_only in _ACK_PATTERNS or (len(_compact) <= 10 and _compact in _ACK_REPEATS):
            eid = (envelope.get("id", "?"))[:8]
            self.logger.info(f" [{eid}] ACK skip: '{stripped[:20]}' (送达已由传输层确认)")
            return

        from_id = envelope.get("from", "")
        # 620: 移除 envelope.get("from_id") 容错回退
        if not from_id:
            return
        msg_id = envelope.get("id", str(uuid.uuid4()))

        # ── U-007: 群聊 reply_to 过滤 ──
        # 群聊消息若明确回复他人消息，而我方未发过该消息 → 跳过 dispatch（只 observe）
        if not is_dm:
            reply_to = (envelope.get("meta") or {}).get("reply_to", "")
            if reply_to and reply_to not in self._my_msg_ids:
                self.logger.debug(f" [GRP-FILTER] reply_to={reply_to[:8]} 非我方消息, 跳过 dispatch")
                return

        # 安全过滤 — 认证链
        if not await self.security.authenticate(from_id, token=payload.get("token", ""), msg_id=msg_id, envelope=envelope):
            return

        # ── U-005 双层去重 ──
        # L1: msg_id 级去重（精确，覆盖同一消息的重复投递）
        msg_id = envelope.get("id", "")
        if msg_id and msg_id in self._processed_ids:
            self.logger.debug(f" [DEDUP L1] msg_id={msg_id[:8]} 已处理, 跳过")
            return
        now_ts = time.time()
        # L2: 内容去重（StallWatchdog 重投会换 msg_id，内容查重兜底）
        dedup_key = f"{from_id}:{content[:200]}"
        if dedup_key in self._seen_msg_keys:
            age = now_ts - self._seen_msg_keys[dedup_key]
            if age < 120.0:  # U-005: 5s→120s，覆盖 StallWatchdog 30s 重试周期
                self.logger.info(f" [DEDUP L2] from={from_id} content_dup age={age:.0f}s, 跳过")
                return
        # 记录
        if msg_id:
            self._processed_ids.add(msg_id)
        self._seen_msg_keys[dedup_key] = now_ts
        # 限制去重集合大小
        if len(self._processed_ids) > 2000:
            self._processed_ids = set(list(self._processed_ids)[-500:])
        if len(self._dispatched_ids) > 2000:
            self._dispatched_ids = set(list(self._dispatched_ids)[-500:])
        if len(self._seen_msg_keys) > 500:
            old_keys = sorted(self._seen_msg_keys, key=lambda k: self._seen_msg_keys[k])[:250]
            for k in old_keys:
                del self._seen_msg_keys[k]

        # Phase 1: 识别 Task
        is_task = payload.get("task") is not None or content.startswith("/task ")
        if is_task:
            task_id = envelope.get("task_id", str(uuid.uuid4())[:8])
            self.logger.info(f" 任务入队: {task_id} from={from_id}")
            # TODO Phase 2: AIMTask lifecycle tracking

        msg = Message(
            msg_id=msg_id, from_id=from_id, to_id=self.agent_id,
            grp_id="" if is_dm else (getattr(self, '_default_group', None) or "grp_trio"), msg_type="dm" if is_dm else "grp",
            content=content, raw_envelope=envelope,
        )
        self.queue.enqueue(msg)
        self._dispatch_event.set()
        self.scheduler.on_message_enqueued()

    def _validate_envelope(self, envelope: dict):
        """620: veritas v1.0 信封格式校验

        返回 (severity, reason) 或 None (合规)。
        - hard: ver缺失、from缺失、payload非dict → Phase 2 reject
        - soft: content在顶层、from_id旧字段 → Phase 1 warn

        三方共识：吉量SDK校验 + 呱呱handler清容错 + 火鸡儿E2E验证
        标准文档：~/shared/aim/specs/aim-envelope-spec.md
        """
        # hard: ver 缺失
        if "ver" not in envelope:
            return ("hard", "missing 'ver' field")

        # hard: from 缺失
        if not envelope.get("from"):
            return ("hard", "missing 'from' field")

        # hard: payload 非 dict
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return ("hard", "payload is not a dict")

        # soft: content 在 envelope 顶层而非 payload.text (兼容旧格式)
        if "content" in envelope and "text" not in payload:
            return ("soft", "content at envelope level, expected payload.text")

        # soft: 使用旧字段名 from_id 而非 from
        if "from_id" in envelope and "from" not in envelope:
            return ("soft", "using legacy 'from_id' instead of 'from'")

        return None

    async def _register_with_registry(self):
        """向 Registry 注册本 Agent"""
        try:
            card_data = {
                "name": self.card.name,
                "execution_model": self.card.execution_model,
                "protocol_version": "1.0",
            }
            result = await asyncio.wait_for(
                self.registry_client.register(self.agent_id, card_data),
                timeout=10.0
            )
            self.logger.info(f"Registry 注册: {result.get('action')} serial={result.get('serial')}")
        except asyncio.TimeoutError:
            self.logger.warning("Registry 注册超时（Registry 可能未运行）")
        except Exception as e:
            self.logger.warning(f"Registry 注册失败（Registry 可能未运行）: {e}")

    # --------------- 619-09: SIGHUP 配置重载 ---------------
    def _reload_config(self):
        """SIGHUP 触发的优雅重载：重读 config.json + 重新加载资源"""
        try:
            with open(self.config_path) as f:
                self.config = json.loads(f.read())
            self.logger.info(f"🔄 [619-09] 配置已重载 ({self.agent_id})")
        except Exception as e:
            self.logger.error(f"🔄 [619-09] 配置重载失败: {e}")

    def _shutdown(self):
        self.logger.info("收到终止信号，正在退出...")
        self.running = False
        # P2: 5s 安全网，防止 drain hang 导致僵尸
        signal.alarm(10)

    async def close(self):
        self.running = False
        await asyncio.sleep(0.5)  # 等 _health_probe_loop / _dispatch_loop 退出
        await self.queue.close_persist()
        await self.transport.disconnect()
        self.lock.release()


# -- 异常类 --
class RetryableError(Exception):
    """可重试错误 (exit=1): session 忙、排队中等"""
    pass



# P1-2: DEGRADE 滑动窗口（30s 内 2 次 exit=2 才触发）
DEGRADE_WINDOW_S = 30
DEGRADE_WINDOW_COUNT = 2
class DegradeError(Exception):
    """降级错误 (exit=2): Runtime 不可用"""
    pass


class HumanInterventionError(Exception):
    """需人工介入 (exit=3): 权限不足、框架崩溃"""
    pass


# -- CLI --
async def _run_services(args):
    """--service 模式：启动 Registry + GroupAdmission 服务"""
    from registry import Registry
    from group_admission import GroupAdmission

    nats_url = args.nats_url or "nats://127.0.0.1:4222"
    creds = args.credentials or ""
    registry = Registry(nats_url=nats_url, credentials=creds)
    group_admission = GroupAdmission(nats_url=nats_url, credentials=creds)
    
    # start() 返回后 subscriptions 持续生效，需保持 event loop
    await registry.start()
    await group_admission.start_service()
    
    logger = logging.getLogger("aim-client")
    logger.info("🛡️  服务模式: Registry + GroupAdmission 已启动")
    
    try:
        while True:
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        pass
    finally:
        for svc in [registry, group_admission]:
            try:
                await svc.stop()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=f"AIM Client -- 统一通信终端 v{_AIM_VERSION}")
    parser.add_argument("--agent-id", required=True, help="Agent ID")
    parser.add_argument("--config", required=True, help="config.json 路径")
    parser.add_argument("--nats-url", default=None, help="NATS 服务器地址")
    parser.add_argument("--mode", default="direct", choices=["direct", "service"])
    parser.add_argument("--services", action="store_true", default=False,
                       help="同时启动 Registry + GroupAdmission 服务")
    parser.add_argument("--credentials", default="", help="NATS credentials file")
    args = parser.parse_args()

    if args.mode == "service" or args.services:
        asyncio.run(_run_services(args))
        return

    client = AIMClient(args.config)
    try:
        asyncio.run(client.start())
    except KeyboardInterrupt:
        print(f"\n[aim-client] {args.agent_id} 中断")
    finally:
        try:
            asyncio.run(client.close())
        except Exception:
            pass


if __name__ == "__main__":
    main()
