#!/usr/bin/env python3
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
  - V3 降级为兼容模式 (--mode legacy)

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
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# -- 路径注入 --
SHARED_AIM = Path.home() / "shared" / "aim"
SDK_DIR = Path.home() / ".aim" / "bin"
WORKSPACE = Path.home() / ".openclaw" / "workspace"

for p in [SDK_DIR, WORKSPACE, SHARED_AIM]:
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

# AIM Client 内部模块
from security import SecurityManager
from registry import Registry

# -- QueueProcessor: 标准队列处理器（文件队列模式 adapter 专用） --
class QueueProcessor:
    """标准 Queue Processor — 文件队列模式 adapter 专用

    aim-client 内置模块，不依赖外部 cron/平台特性。
    通过 config.queue_processor.enabled 控制开关。

    工作原理:
      1. 每 poll_interval_s 秒检查 .aim-trigger
      2. 检测到 trigger → 读取 .aim-queue/ 最旧消息
      3. 调用 adapter.sh generate-reply → adapter 直调框架 AI → 写 reply 文件
      4. adapter process 模式的 poll 读取 reply → 返回
    """

    def __init__(self, config, adapter_cmd, adapter_env, workspace, logger):
        qp_cfg = config.get("queue_processor", {})
        self.enabled = qp_cfg.get("enabled", False)
        self.poll_interval = float(qp_cfg.get("poll_interval_s", 2))
        self.adapter_cmd = adapter_cmd
        self.adapter_env = adapter_env
        self.workspace = Path(workspace).expanduser()
        self.logger = logger
        self._trigger_file = self.workspace / ".aim-trigger"
        self._queue_dir = self.workspace / ".aim-queue"
        self._reply_dir = self.workspace / ".aim-replies"
        if self.enabled:
            self.logger.info(f"[QueueProcessor] 启用 poll={self.poll_interval}s ws={self.workspace}")

    async def run(self):
        """事件循环入口，由 AIMClient.start() 作为 async task 启动"""
        if not self.enabled:
            return
        while True:
            await asyncio.sleep(self.poll_interval)
            try:
                if self._trigger_file.exists():
                    self.logger.info("[QP] trigger detected, processing...")
                    await self._process_one()
            except Exception as e:
                self.logger.error(f"[QP] 异常: {e}")

    async def _process_one(self):
        """处理一个队列项"""
        items = sorted(self._queue_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not items:
            try:
                self._trigger_file.unlink(missing_ok=True)
            except OSError:
                pass
            return

        item_path = items[0]
        try:
            data = json.loads(item_path.read_text())
        except Exception:
            item_path.unlink(missing_ok=True)
            return

        msg_id = data.get("msg_id", "")
        content = data.get("content", "")
        from_id = data.get("from", "unknown")
        safe_content = content.replace("'", "'\\''")
        safe_from = from_id.replace("'", "'\\''")
        cmd = (
            f"bash {self.adapter_cmd} generate-reply "
            f"--msg-id '{msg_id}' --from '{safe_from}' --content '{safe_content}'"
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=self.adapter_env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=30.0
                )
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
                self.logger.warning(f" [QP] 超时: {msg_id[:8]}")
                self._write_reply(msg_id, "")
                item_path.unlink(missing_ok=True)
                return

            if proc.returncode == 0 and stdout:
                reply = stdout.decode().strip()
                self._write_reply(msg_id, reply)
                self.logger.debug(f" [QP] 已回复 {msg_id[:8]} -> {reply[:50]}")
            else:
                err = stderr.decode()[:100] if stderr else ""
                self.logger.warning(f" [QP] 失败 rc={proc.returncode}: {err}")
                self._write_reply(msg_id, "")

            item_path.unlink(missing_ok=True)

        except Exception as e:
            self.logger.error(f" [QP] 异常: {e}")
            self._write_reply(msg_id, "")
            item_path.unlink(missing_ok=True)

    def _write_reply(self, msg_id, text):
        os.makedirs(self._reply_dir, exist_ok=True)
        (self._reply_dir / f"{msg_id}.txt").write_text(text)


# -- 单实例互斥 --
LOCK_DIR = Path.home() / ".aim" / "run"
LOCK_DIR.mkdir(parents=True, exist_ok=True)


class SingleInstance:
    """单实例互斥：PID 检查前置 + fcntl.flock 竞态防护。

    策略（PID 优先）：
    1. 读锁文件 PID → 存活则 SIGTERM(3s)→SIGKILL 强杀 → unlink
    2. 创建新锁文件 + fcntl.flock（防同时启动的竞态窗口）
    3. 写入自身 PID
    """

    def __init__(self, agent_id: str):
        self.lock_file = LOCK_DIR / f"aim-client-{agent_id}.lock"
        self.fp: Optional[object] = None
        self.agent_id = agent_id

    def acquire(self) -> bool:
        # 第一步：pgrep 扫描所有同名旧进程（兜底漏网之鱼）
        self._pgrep_kill_old_instances()
        # 第二步：检查锁文件 PID 并清理
        self._kill_old_process_if_alive()
        
        # 第三步：获取文件锁（防竞态）
        try:
            self.fp = open(self.lock_file, "w")
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            return True
        except (IOError, OSError):
            return self._try_recover_stale()

    def _pgrep_kill_old_instances(self):
        """pgrep 扫描同 agent-id 的所有旧进程，杀之"""
        import subprocess
        try:
            pattern = f"main.py.*--agent-id {self.agent_id}"
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0 or not result.stdout.strip():
                return
            my_pid = os.getpid()
            for line in result.stdout.strip().split("\n"):
                try:
                    old_pid = int(line.strip())
                    if old_pid == my_pid:
                        continue
                    os.kill(old_pid, signal.SIGTERM)
                except (ValueError, OSError):
                    continue
            time.sleep(3)
            for line in result.stdout.strip().split("\n"):
                try:
                    old_pid = int(line.strip())
                    if old_pid == my_pid:
                        continue
                    os.kill(old_pid, 0)
                    os.kill(old_pid, signal.SIGKILL)
                except (ValueError, OSError):
                    continue
            time.sleep(0.5)
        except Exception:
            pass  # pgrep 不可用时静默跳过

    def _kill_old_process_if_alive(self):
        """检查锁文件中的 PID，存活则杀，最后清理旧锁"""
        try:
            if not self.lock_file.exists():
                return
            old_pid_str = self.lock_file.read_text().strip()
            old_pid = int(old_pid_str)
            os.kill(old_pid, 0)  # 检查进程是否存在
            # PID 存活 → 杀旧接管
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(3)
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)
                time.sleep(0.5)
            except OSError:
                pass
        except (ValueError, OSError):
            pass
        # 无论如何清理旧锁文件，确保干净状态
        self.lock_file.unlink(missing_ok=True)

    def _try_recover_stale(self) -> bool:
        """flock 竞态兜底：某个进程抢先获取了锁，尝试杀它接管"""
        self._kill_old_process_if_alive()
        try:
            self.fp = open(self.lock_file, "w")
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            return True
        except (IOError, OSError):
            return False

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
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(sh)
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
        await self._sdk.close()

    async def subscribe_dm(self, handler):
        await self._sdk.subscribe_dm(handler)
        self._logger.info(f" 已订阅私聊: aim.dm.{self.agent_id}")

    async def subscribe_grp(self, group_id: str, handler):
        await self._sdk.subscribe_grp(group_id, handler)
        self._logger.info(f" 已订阅群聊: aim.grp.{group_id}")

    async def send_dm(self, to_id: str, text: str):
        """发送私聊消息"""
        await self._sdk.send_dm(to_id, text)

    async def send_grp(self, group_id: str, text: str):
        """发送群聊消息"""
        await self._sdk.send_grp(group_id, text)

    async def send_ack(self, to_id: str, original_msg_id: str):
        """发送已读回执（type: ack）"""
        import json as _json
        from aim_nats_sdk import make_envelope, Subjects
        envelope = make_envelope(
            from_id=self.agent_id, msg_type="ack",
            payload={"text": ""}, reply_to=original_msg_id,
        )
        subject = Subjects.dm(to_id)
        data = _json.dumps(envelope, ensure_ascii=False).encode()
        await self._sdk.nc.publish(subject, data)
        self._logger.debug(f" ACK → {to_id} (msg={original_msg_id[:8]})")

    async def authenticate(self) -> bool:
        return True

    async def verify_peer(self, peer_id: str) -> bool:
        cfg = load_global_config()
        peers = cfg.get("trusted_peers", ["ZS0001"])
        return peer_id in peers


# -- AIMClient 主类 --
class AIMClient:
    """AIM Client 主进程 -- Phase 1"""

    def __init__(self, config_path: str):
        config_path = Path(config_path).expanduser()
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
        self.registry_client = Registry(nats_url)

        # Phase 0: Queue + Scheduler + HealthProbe
        self.queue = MessageQueue(capacity=self.config.get("queue_capacity", 1000))
        self.scheduler = Scheduler(
            processing_timeout=self.config.get("adapter_timeout", 120),
            health_probe_interval=5.0,
        )
        adapter_cmd = self.config.get("adapter_cmd", "")
        if adapter_cmd:
            adapter_cmd = str(Path(adapter_cmd).expanduser())
        self.adapter_cmd = adapter_cmd

        # 从 config.env 读取环境变量，传递 adapter（解决 HERMES_BIN/LETTA_BIN 等问题）
        self.adapter_env = os.environ.copy()
        for k, v in self.config.get("env", {}).items():
            self.adapter_env[k] = str(Path(v).expanduser()) if v else v

        self.health_probe = HealthProbe(
            health_cmd=f"bash {self.adapter_cmd} health",
            timeout=10.0,
            env=self.adapter_env,
        )

        # 工作目录路径（QueueProcessor 需要）
        self.workspace_path = str(
            Path(self.config.get("paths", {}).get(
                "workspace", "~/.openclaw/workspace"
            )).expanduser()
        )

        self.running = False
        self.logger.info(f"Framework: {self.config.get('framework', 'unknown')}")
        self.logger.info(f"Adapter: {self.adapter_cmd}")
        self.logger.info(f"Queue+Scheduler 已嵌入 (capacity={self.queue.capacity})")

    async def start(self):
        if not self.lock.acquire():
            self.logger.error("另一个 aim-client 已在运行")
            sys.exit(1)

        atexit.register(self.lock.release)
        signal.signal(signal.SIGTERM, lambda *_: self._shutdown())

        if not await self.transport.connect():
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
        self.logger.info(f" {self.agent_id} AIM Client v1.0.0 启动完成")

        # 健康探针循环
        asyncio.create_task(self._health_probe_loop())

        # QueueProcessor（标准模块，config.queue_processor.enabled 控制）
        self.queue_processor = QueueProcessor(
            self.config, self.adapter_cmd, self.adapter_env,
            self.workspace_path, self.logger,
        )
        asyncio.create_task(self.queue_processor.run())

        # 主循环保持
        while self.running:
            await asyncio.sleep(5)

    async def _health_probe_loop(self):
        """Phase 0: 健康探针 -> 更新 Scheduler -> 触发投递"""
        while self.running:
            try:
                report = await self.health_probe.probe()
                self.scheduler.update_state(report)
                await self._try_dispatch()
            except Exception as e:
                self.logger.error(f"健康探针异常: {e}")
            interval = self.scheduler.get_probe_interval()
            await asyncio.sleep(interval)

    async def _try_dispatch(self):
        """Scheduler 驱动的消息投递"""
        while self.scheduler.should_dispatch() and self.queue.size() > 0:
            msg = self.queue.dequeue()
            if not msg:
                break
            self.scheduler.on_dispatch_started()
            self.logger.info(f" 投递: {msg.msg_id[:8]} from={msg.from_id} (q={self.queue.size()})")
            # 已读回执：出队即发送（WeChat 已读语义）
            try:
                await self.transport.send_ack(msg.from_id, msg.msg_id)
            except Exception as ack_err:
                self.logger.debug(f" ACK 发送失败（不阻塞）: {ack_err}")
            try:
                reply = await self._call_adapter(msg)
                if reply:
                    if msg.grp_id:
                        await self.transport.send_grp(msg.grp_id, reply)
                        self.logger.info(f" [{msg.msg_id[:8]}] 已回复群聊 {msg.grp_id}")
                    else:
                        await self.transport.send_dm(msg.from_id, reply)
                        self.logger.info(f" [{msg.msg_id[:8]}] 已回复 {msg.from_id}")
                else:
                    self.logger.info(f" [{msg.msg_id[:8]}] 空回复，不发送")
                self.scheduler.on_processing_done()
                self.queue.ack(msg.msg_id)
            except RetryableError:
                self.logger.warning(f" [{msg.msg_id[:8]}] 可重试，exit=1")
                self.scheduler.on_retry()
                if msg.retry_count < 3:
                    msg.retry_count += 1
                    # nack 会将消息放回队头 + 清除 _processing
                    self.queue.nack(msg.msg_id, "retryable")
                    self.logger.info(f" RETRY #{msg.retry_count}/3")
                else:
                    # 超过重试次数
                    self.queue.nack(msg.msg_id, "max_retries")
                    self.logger.info(f" MAX RETRIES exceeded for {msg.msg_id[:8]}")
            except DegradeError:
                self.logger.error(f" [{msg.msg_id[:8]}] 降级 (exit=2)")
                self.scheduler.on_degrade()
                self.queue.nack(msg.msg_id, "degrade")
            except HumanInterventionError:
                self.logger.error(f" [{msg.msg_id[:8]}] 需人工介入 (exit=3)")
                self.scheduler.on_human_intervention()
                self.queue.nack(msg.msg_id, "human_intervention")
            except Exception as e:
                self.logger.error(f" [{msg.msg_id[:8]}] 投递异常: {e}")
                self.scheduler.on_timeout()
                if msg.retry_count < 3:
                    msg.retry_count += 1
                    self.queue.nack(msg.msg_id, str(e))
                    self.logger.info(f" RETRY #{msg.retry_count}/3")
                else:
                    self.queue.nack(msg.msg_id, "max_retries")
                if msg.retry_count < 3:
                    msg.retry_count += 1
                    self.queue.enqueue(msg)

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
            # 降级（exit=2）：Runtime 不可用
            raise DegradeError(stderr_text or f"adapter exit=2")

        elif rc == 3:
            # 人工介入（exit=3）：权限不足、框架崩溃等
            raise HumanInterventionError(stderr_text or f"adapter exit=3")

        else:
            raise RuntimeError(f"adapter exit={rc}: {stderr_text[:100]}")

    # -- NATS 回调 (SDK 签名: handler(envelope_dict, raw_msg)) --

    async def _on_dm(self, envelope: dict, raw_msg):
        from_id = envelope.get("from", envelope.get("from_id", ""))
        self.logger.info(f" DM收到: from={from_id} id={str(envelope.get('id','?'))[:8]}")
        if from_id == self.agent_id:
            return
        await self._handle_message(envelope, is_dm=True)

    async def _on_grp(self, envelope: dict, raw_msg):
        from_id = envelope.get("from", envelope.get("from_id", ""))
        self.logger.info(f" GRP收到: from={from_id}")
        if from_id == self.agent_id:
            return
        await self._handle_message(envelope, is_dm=False)

    async def _handle_message(self, envelope: dict, *, is_dm: bool):
        payload = envelope.get("payload", {})
        content = payload.get("text", "") or envelope.get("content", "")
        if not content:
            return

        from_id = envelope.get("from", envelope.get("from_id", ""))

        # 安全过滤
        if not self.security.allow(from_id): return
        if not self.security.rate_ok(from_id): return
        msg_id = envelope.get("id", str(uuid.uuid4()))

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
        self.scheduler.on_message_enqueued()

        if self.scheduler.is_idle:
            await self._try_dispatch()

    async def _register_with_registry(self):
        """向 Registry 注册本 Agent"""
        try:
            card_data = {
                "name": self.card.name,
                "execution_model": self.card.execution_model,
                "protocol_version": "1.0",
            }
            result = await self.registry_client.register(self.agent_id, card_data)
            self.logger.info(f"Registry 注册: {result.get('action')} serial={result.get('serial')}")
        except Exception as e:
            self.logger.warning(f"Registry 注册失败（Registry 可能未运行）: {e}")

    def _shutdown(self):
        self.logger.info("收到终止信号，正在退出...")
        self.running = False

    async def close(self):
        await self.transport.disconnect()
        self.lock.release()


# -- 异常类 --
class RetryableError(Exception):
    """可重试错误 (exit=1): session 忙、排队中等"""
    pass


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
    registry = Registry(nats_url=nats_url)
    group_admission = GroupAdmission(nats_url=nats_url)
    
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
    parser = argparse.ArgumentParser(description="AIM Client -- 统一通信终端 v1.0.0")
    parser.add_argument("--agent-id", required=True, help="Agent ID")
    parser.add_argument("--config", required=True, help="config.json 路径")
    parser.add_argument("--nats-url", default=None, help="NATS 服务器地址")
    parser.add_argument("--mode", default="direct", choices=["direct", "legacy", "service"])
    parser.add_argument("--services", action="store_true", default=False,
                       help="同时启动 Registry + GroupAdmission 服务")
    args = parser.parse_args()

    if args.mode == "legacy":
        print(f"[aim-client] 降级到 V3 兼容模式")
        from v3_compat import run_v3_mode
        asyncio.run(run_v3_mode(args.agent_id, args.config))
        return

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
