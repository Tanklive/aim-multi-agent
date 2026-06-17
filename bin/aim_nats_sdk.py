#!/usr/bin/env python3
"""
AIM NATS SDK（Veritas 协议 v1.0）
统一的 NATS 客户端封装 + Pin 去重 + RetryManager 重试

协议规范：
  - Subject: aim.dm.<id> / aim.grp.<id> / aim.reg.register / aim.obs.<id> / aim.sys.<event>
  - 消息信封：{ver, id, ts, from, type, payload, meta?, sig?}
  - JetStream Stream: aim-messages / aim-observations / aim-system

Usage:
    from aim_nats_sdk import AIMNATSClient, AIMMessage, Subjects
    client = AIMNATSClient("ZS0002")
    await client.connect()
    await client.subscribe_dm(on_msg)
    await client.send_dm("ZS0001", "你好呱呱")
"""

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

# ── 鉴权解析 ────────────────────────────────────────────────────


def _resolve_credentials(cfg: dict, agent_id: str = "") -> str:
    """从配置中解析最优凭证

    优先级:
      1. 配置文件中 agents[agent_id].creds_path（Agent 专属 .creds 文件）
      2. 配置文件中 nats_jwt_path（全局 .creds / .nkey 文件路径）
      3. 配置文件中 nats_token（Token 字符串）
      4. 空字符串（裸连调试）

    Args:
        cfg: 配置字典
        agent_id: Agent ID，用于读取 agents 下专属 creds 路径

    Returns:
        credentials 字符串（文件路径或 Token）
    """
    # 1. Agent 专属 creds 路径
    if agent_id:
        agents = cfg.get("agents", {})
        agent_cfg = agents.get(agent_id, {})
        creds_path = agent_cfg.get("creds_path", "")
        if creds_path:
            expanded = os.path.expanduser(creds_path)
            if os.path.isfile(expanded):
                return expanded

    # 2. 全局 nats_jwt_path
    jwt_path = cfg.get("nats_jwt_path", "")
    if jwt_path:
        expanded = os.path.expanduser(jwt_path)
        if os.path.isfile(expanded):
            return expanded

    # 3. Token 回退
    return cfg.get("nats_token", "")

try:
    import nats
    from nats.aio.client import Client as NATSClient
    from nats.js import JetStreamContext
    from nats.js.api import StreamConfig, ConsumerConfig
except ImportError:
    raise ImportError("pip install nats-py")

log = logging.getLogger("aim-nats")


# ════════════════════════════════════════════════════════════════════
#  消息信封
# ════════════════════════════════════════════════════════════════════

def make_msg_id() -> str:
    """生成短唯一消息 ID"""
    return uuid.uuid4().hex[:12]


def make_envelope(
    from_id: str,
    msg_type: str,
    payload: Dict[str, Any],
    reply_to: str = "",
    msg_id: str = "",
) -> Dict[str, Any]:
    """创建标准 AIM 消息信封（aim-veritas §4.8）"""
    return {
        "ver": "1.0",
        "id": msg_id or make_msg_id(),
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "from": from_id,
        "type": msg_type,
        "payload": payload,
        **(  {"meta": {"reply_to": reply_to}} if reply_to else {}),
    }


def parse_message(data: bytes) -> Dict[str, Any]:
    """解析 AIM 消息"""
    return json.loads(data.decode())


# ════════════════════════════════════════════════════════════════════
#  Subject 命名
# ════════════════════════════════════════════════════════════════════

class Subjects:
    """AIM NATS Subject 命名规范"""

    @staticmethod
    def dm(agent_id: str) -> str:
        return f"aim.dm.{agent_id}"

    @staticmethod
    def grp(group_id: str) -> str:
        return f"aim.grp.{group_id}"

    @staticmethod
    def obs(agent_id: str) -> str:
        return f"aim.obs.{agent_id}"

    @staticmethod
    def obs_all() -> str:
        return "aim.obs.>"

    @staticmethod
    def reg_register() -> str:
        return "aim.reg.register"

    @staticmethod
    def sys_event(event: str) -> str:
        return f"aim.sys.{event}"

    @staticmethod
    def sys_all() -> str:
        return "aim.sys.>"

    # ── Agent Card（Discovery 用）──

    @staticmethod
    def card(global_id: str) -> str:
        """Agent Card KV key（NATS KV bucket: aim-cards）"""
        return global_id

    @staticmethod
    def card_event(event: str) -> str:
        """Agent Card 事件通知 aim.events.card.{event}"""
        return f"aim.events.card.{event}"

    @staticmethod
    def card_bucket() -> str:
        """Agent Card 的 NATS KV bucket 名称"""
        return "aim-cards"


# ════════════════════════════════════════════════════════════════════
#  Observer 事件类型枚举（三方统一标准）
# ════════════════════════════════════════════════════════════════════

class ObsEventType:
    """Observer 事件类型常量（三方统一标准）

    标准生命周期序列：received → processing → ai_start → ai_done/ai_empty → completed/error

    用法:
        from aim_nats_sdk import ObsEventType
        await client.emit_obs(ObsEventType.RECEIVED, msg_id, "收到消息")
    """
    # 消息生命周期
    RECEIVED = "received"       # 收到消息，去重检查通过
    PROCESSING = "processing"   # 进入处理流程
    COMPLETED = "completed"     # 回复已完成并发送
    ERROR = "error"             # 异常捕获

    # AI 处理过程
    AI_START = "ai_start"       # 调用 AI 框架
    AI_DONE = "ai_done"         # AI 返回非空回复
    AI_EMPTY = "ai_empty"       # AI 返回空内容

    # 系统事件
    AGENT_ONLINE = "agent_online"     # Agent 上线
    AGENT_OFFLINE = "agent_offline"   # Agent 下线
    HEARTBEAT = "heartbeat"           # 心跳

    @classmethod
    def all_events(cls) -> set:
        """返回所有标准事件类型的 set"""
        return {
            cls.RECEIVED, cls.PROCESSING, cls.COMPLETED, cls.ERROR,
            cls.AI_START, cls.AI_DONE, cls.AI_EMPTY,
            cls.AGENT_ONLINE, cls.AGENT_OFFLINE, cls.HEARTBEAT,
        }

    @classmethod
    def lifecycle_events(cls) -> list:
        """消息生命周期事件序列（含分支）"""
        return [cls.RECEIVED, cls.PROCESSING, cls.AI_START, cls.AI_DONE, cls.AI_EMPTY, cls.COMPLETED, cls.ERROR]


# ════════════════════════════════════════════════════════════════════
#  RateLimiter — 滑动窗口限流器
# ════════════════════════════════════════════════════════════════════

class RateLimiter:
    """滑动窗口限流器 — 按 agent_id 限流

    用法:
        limiter = RateLimiter(max_per_second=5)
        if limiter.check("ZS0002"):
            await do_something()
    """

    def __init__(self, max_per_second: int = 3):
        self.max_per_second = max_per_second
        self._windows: Dict[str, list] = {}

    def check(self, agent_id: str) -> bool:
        """检查是否允许通过，返回 True=允许，False=限流"""
        now = time.time()
        # 清理超过 1 秒的记录
        window = self._windows.setdefault(agent_id, [])
        self._windows[agent_id] = [t for t in window if now - t < 1.0]
        if len(self._windows[agent_id]) >= self.max_per_second:
            return False
        self._windows[agent_id].append(now)
        return True

    def remaining(self, agent_id: str) -> int:
        """该 agent 剩余可用配额"""
        now = time.time()
        window = self._windows.get(agent_id, [])
        window = [t for t in window if now - t < 1.0]
        return max(0, self.max_per_second - len(window))

    def reset(self, agent_id: str = ""):
        """重置限流"""
        if agent_id:
            self._windows.pop(agent_id, None)
        else:
            self._windows.clear()


# ════════════════════════════════════════════════════════════════════
#  SecureMessage — 消息签名与防重放
# ════════════════════════════════════════════════════════════════════

class SecureMessage:
    """消息签名与防重放

    对 emit_obs / send_dm / send_grp 消息进行 HMAC 签名，
    防止伪造和重放攻击。

    用法:
        sm = SecureMessage(secret="my-shared-secret")
        msg = sm.sign({"msg_id": "xxx", "status": "processing"})
        # ... 发送 msg ...
        if sm.verify(received_msg):
            # 消息合法
            pass
    """

    MAX_AGE = 30  # 消息最大有效时间（秒）

    def __init__(self, secret: str = ""):
        self.secret = secret
        self._used_nonces: set = set()

    def sign(self, msg: dict) -> dict:
        """对消息签名，加入 nonce + timestamp"""
        import hashlib
        import hmac as _hmac

        msg["nonce"] = uuid.uuid4().hex[:16]
        msg["timestamp"] = time.time()

        if self.secret:
            payload = f"{msg.get('id', '')}:{msg['nonce']}:{int(msg['timestamp'])}"
            sig = _hmac.new(
                self.secret.encode(),
                payload.encode(),
                hashlib.sha256,
            ).hexdigest()
            msg["sig"] = sig

        return msg

    def verify(self, msg: dict) -> bool:
        """验证消息签名 + 防重放"""
        # 必须有 nonce 和 timestamp
        nonce = msg.get("nonce", "")
        ts = msg.get("timestamp", 0)
        if not nonce or not ts:
            return False

        # 时间窗口检查
        if time.time() - ts > self.MAX_AGE:
            return False

        # 防重放（nonce 唯一性）
        if nonce in self._used_nonces:
            return False
        self._used_nonces.add(nonce)

        # 如果没有密钥，跳过签名验证（仅做防重放）
        if not self.secret:
            return True

        # HMAC 签名验证
        import hashlib
        import hmac as _hmac

        sig = msg.get("sig", "")
        if not sig:
            return False

        payload = f"{msg.get('id', '')}:{nonce}:{int(ts)}"
        expected = _hmac.new(
            self.secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        return _hmac.compare_digest(sig, expected)


# ════════════════════════════════════════════════════════════════════
#  AIMPin — 消息去重组件（持久化 LRU + TTL）
# ════════════════════════════════════════════════════════════════════

class AIMPin:
    """消息去重 Pin 组件 — 持久化 LRU + TTL

    防止 Agent 重复处理同一条消息（网络重传、NATS 重连、多订阅等场景）。

    Usage:
        pin = AIMPin(agent_id="ZS0002", ttl=120)
        if not await pin.is_duplicate(msg_id):
            await pin.mark(msg_id)
            # process message
    """

    DEFAULT_TTL = 120        # 呱呱建议：与 JetStream duplicate_window 一致
    MAX_MEMORY = 2000        # 内存缓存上限
    PERSIST_INTERVAL = 60    # 持久化写入间隔（秒）

    def __init__(
        self,
        agent_id: str,
        ttl: int = 120,
        db_dir: str = "",
        max_memory: int = 2000,
    ):
        self.agent_id = agent_id
        self.ttl = ttl or self.DEFAULT_TTL
        self.max_memory = max_memory or self.MAX_MEMORY

        db_dir = db_dir or str(Path.home() / ".hermes" / "aim" / "data")
        Path(db_dir).mkdir(parents=True, exist_ok=True)
        self._db_path = f"{db_dir}/pin_{agent_id}.db"
        self._cache: Dict[str, float] = {}
        self._persisted: Set[str] = set()
        self._lock = asyncio.Lock()

        self.stats = {"hits": 0, "misses": 0, "persisted": 0, "evicted": 0}
        self._init_db()

    # ── 数据库 ────────────────────────────────────────────

    def _init_db(self):
        try:
            conn = sqlite3.connect(self._db_path, timeout=5)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pins (
                    msg_id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    ttl REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pins_ts ON pins(ts)")
            conn.commit()
            self._cleanup_db(conn)
            conn.close()
        except Exception as e:
            log.warning(f"[Pin:{self.agent_id}] DB init failed: {e}")

    def _cleanup_db(self, conn: sqlite3.Connection):
        try:
            now = time.time()
            cursor = conn.execute("DELETE FROM pins WHERE ts + ttl < ?", (now,))
            deleted = cursor.rowcount
            if deleted > 0:
                conn.commit()
        except Exception:
            pass

    def _persist_batch(self, entries: Dict[str, float]):
        if not entries:
            return
        try:
            now = time.time()
            conn = sqlite3.connect(self._db_path, timeout=5)
            data = [
                (mid, ts, self.ttl, now)
                for mid, ts in entries.items()
                if mid not in self._persisted
            ]
            if data:
                conn.executemany(
                    "INSERT OR IGNORE INTO pins (msg_id, ts, ttl, created_at) VALUES (?, ?, ?, ?)",
                    data,
                )
                conn.commit()
                self.stats["persisted"] += len(data)
                for mid, _, _, _ in data:
                    self._persisted.add(mid)
            conn.close()
        except Exception as e:
            log.warning(f"[Pin:{self.agent_id}] persist failed: {e}")

    # ── 核心去重接口 ─────────────────────────────────────

    async def is_duplicate(self, msg_id: str) -> bool:
        """检查 msg_id 是否已处理过（重复 = True）"""
        async with self._lock:
            now = time.time()

            # 1. 内存缓存
            ts = self._cache.get(msg_id)
            if ts is not None:
                if now - ts <= self.ttl:
                    self.stats["hits"] += 1
                    return True
                del self._cache[msg_id]

            # 2. DB 持久化
            if msg_id in self._persisted:
                self.stats["hits"] += 1
                return True
            try:
                conn = sqlite3.connect(self._db_path, timeout=3)
                cursor = conn.execute(
                    "SELECT ts FROM pins WHERE msg_id = ? AND ts + ttl > ?",
                    (msg_id, now),
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    self._persisted.add(msg_id)
                    self.stats["hits"] += 1
                    return True
            except Exception:
                pass

            self.stats["misses"] += 1
            return False

    async def mark(self, msg_id: str):
        """标记 msg_id 为已处理（先 is_duplicate 检查通过后调用）"""
        async with self._lock:
            now = time.time()
            self._cache[msg_id] = now
            if len(self._cache) > self.max_memory:
                sorted_items = sorted(self._cache.items(), key=lambda x: x[1])
                evict_count = len(self._cache) - self.max_memory
                for mid, _ in sorted_items[:evict_count]:
                    del self._cache[mid]
                    self.stats["evicted"] += 1

    async def flush(self):
        """将内存缓存刷入持久化存储"""
        async with self._lock:
            if self._cache:
                self._persist_batch(self._cache)

    async def clear(self):
        """清空所有缓存"""
        async with self._lock:
            self._cache.clear()
            self._persisted.clear()
            try:
                conn = sqlite3.connect(self._db_path, timeout=5)
                conn.execute("DELETE FROM pins")
                conn.commit()
                conn.close()
            except Exception:
                pass

    def get_stats(self) -> dict:
        return {**self.stats, "cache_size": len(self._cache), "persisted_set": len(self._persisted), "ttl": self.ttl}


# ════════════════════════════════════════════════════════════════════
#  RetryManager — 消息重试组件（指数退避）
# ════════════════════════════════════════════════════════════════════

@dataclass
class RetryAttempt:
    """一次重试的记录"""
    attempt: int
    started_at: float
    duration: float
    error: str
    traceback: str


@dataclass
class RetryTask:
    """重试任务的状态"""
    task_id: str
    label: str
    fn: Callable
    max_retries: int
    base_delay: float
    max_delay: float
    status: str = "pending"
    attempts: List[RetryAttempt] = field(default_factory=list)
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    last_attempt_at: float = 0.0


class RetryManager:
    """消息重试管理器 — 指数退避 + 最大重试限制

    默认策略（呱呱建议联调微调）：
      max_retries=5, base_delay=1s, max_delay=60s, backoff=2x

    Usage:
        rm = RetryManager(agent_id="ZS0002")
        ok, result = await rm.retry(
            label="send_dm",
            fn=lambda: client.send_dm("ZS0001", "hello"),
        )
    """

    DEFAULT_STRATEGIES = {
        "default":   {"max_retries": 5, "base_delay": 1.0, "max_delay": 60.0},
        "send":      {"max_retries": 5, "base_delay": 1.0, "max_delay": 60.0},
        "connect":   {"max_retries": 5, "base_delay": 2.0, "max_delay": 60.0},
        "subscribe": {"max_retries": 5, "base_delay": 1.0, "max_delay": 30.0},
        "request":   {"max_retries": 3, "base_delay": 0.5, "max_delay": 10.0},
    }

    def __init__(
        self,
        agent_id: str,
        log: logging.Logger = None,
        strategies: Dict[str, dict] = None,
        history_path: str = None,
    ):
        self.agent_id = agent_id
        self.log = log or logging.getLogger(f"retry-{self.agent_id}")
        self.strategies = {**self.DEFAULT_STRATEGIES, **(strategies or {})}
        self._tasks: Dict[str, RetryTask] = {}

        history_path = history_path or str(Path.home() / ".hermes" / "aim" / "data" / f"retry_{agent_id}.jsonl")
        Path(history_path).parent.mkdir(parents=True, exist_ok=True)
        self._history_path = history_path
        self.stats = {"total_retries": 0, "successful": 0, "failed": 0, "total_attempts": 0}

    # ── 延迟计算（2x 指数退避 + 随机抖动） ───────────────

    @staticmethod
    def calc_delay(
        attempt: int,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: float = 0.1,
    ) -> float:
        """delay = min(base * 2^attempt, max) + jitter"""
        delay = min(base_delay * (2.0 ** attempt), max_delay)
        if jitter > 0:
            delay += delay * jitter * (math.sin(attempt * 7) * 0.5 + 0.5)
        return delay

    # ── 核心重试逻辑 ─────────────────────────────────────

    async def retry(
        self,
        label: str,
        fn: Callable,
        max_retries: int = None,
        base_delay: float = None,
        max_delay: float = None,
        strategy: str = "default",
        on_success: Callable = None,
        on_failure: Callable = None,
        timeout: float = None,
    ) -> Tuple[bool, Any]:
        """执行并重试（返回 (success, result_or_error)）"""
        strat = self.strategies.get(strategy, self.strategies["default"])
        max_retries = max_retries if max_retries is not None else strat["max_retries"]
        base_delay = base_delay if base_delay is not None else strat["base_delay"]
        max_delay = max_delay if max_delay is not None else strat["max_delay"]

        task_id = f"retry_{uuid.uuid4().hex[:8]}"
        task = RetryTask(task_id=task_id, label=label, fn=fn,
                         max_retries=max_retries, base_delay=base_delay, max_delay=max_delay)
        self._tasks[task_id] = task
        task.status = "running"

        attempts = 0
        while attempts <= max_retries:
            attempt_start = time.time()
            try:
                if timeout:
                    result = await asyncio.wait_for(fn(), timeout=timeout)
                else:
                    result = await fn()

                task.status = "success"
                task.result = result
                task.last_attempt_at = time.time()
                self.stats["successful"] += 1
                self.stats["total_retries"] += attempts
                self.stats["total_attempts"] += attempts + 1

                task.attempts.append(RetryAttempt(
                    attempt=attempts, started_at=attempt_start,
                    duration=time.time() - attempt_start, error="", traceback=""))
                self._save_history(task)
                if on_success:
                    try:
                        on_success(result)
                    except Exception:
                        pass
                return True, result

            except asyncio.TimeoutError:
                err = f"timeout ({timeout}s)"
                tb = ""
            except Exception as e:
                err = str(e)
                tb = traceback.format_exc()

            duration = time.time() - attempt_start
            task.attempts.append(RetryAttempt(
                attempt=attempts, started_at=attempt_start,
                duration=duration, error=err, traceback=tb))
            task.last_attempt_at = time.time()

            if attempts < max_retries:
                delay = self.calc_delay(attempts, base_delay, max_delay)
                self.log.warning(
                    f"[Retry:{self.agent_id}] {label} "
                    f"attempt {attempts + 1}/{max_retries + 1} failed: {err} "
                    f"(next delay={delay:.1f}s)")
                await asyncio.sleep(delay)
                attempts += 1
            else:
                task.status = "failed"
                task.error = err
                self.stats["failed"] += 1
                self.stats["total_retries"] += attempts
                self.stats["total_attempts"] += attempts + 1
                self.log.error(
                    f"[Retry:{self.agent_id}] {label} "
                    f"failed after {max_retries + 1} attempts: {err}")
                self._save_history(task)
                if on_failure:
                    try:
                        on_failure(err)
                    except Exception:
                        pass
                return False, err

        return False, task.error

    # ── 快捷方法 ─────────────────────────────────────────

    async def retry_send(self, label: str, fn: Callable) -> Tuple[bool, Any]:
        return await self.retry(label, fn, strategy="send")

    async def retry_connect(self, label: str, fn: Callable) -> Tuple[bool, Any]:
        return await self.retry(label, fn, strategy="connect")

    async def retry_request(self, label: str, fn: Callable) -> Tuple[bool, Any]:
        return await self.retry(label, fn, strategy="request")

    # ── 历史记录 ─────────────────────────────────────────

    def _save_history(self, task: RetryTask):
        try:
            record = {
                "agent_id": self.agent_id, "task_id": task.task_id,
                "label": task.label, "status": task.status,
                "max_retries": task.max_retries,
                "attempts_count": len(task.attempts),
                "error": task.error[:200] if task.error else "",
                "duration": task.last_attempt_at - task.created_at if task.last_attempt_at else 0,
                "ts": time.time(),
            }
            with open(self._history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def get_history(self, limit: int = 10) -> List[dict]:
        records = []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except FileNotFoundError:
            pass
        return records[-limit:]

    def get_stats(self) -> dict:
        return {**self.stats,
                "active_tasks": len([t for t in self._tasks.values() if t.status == "running"]),
                "total_tasks": len(self._tasks)}

    def get_task(self, task_id: str) -> Optional[RetryTask]:
        return self._tasks.get(task_id)

    def get_failed_tasks(self) -> List[RetryTask]:
        return [t for t in self._tasks.values() if t.status == "failed"]

    def clear_tasks(self):
        self._tasks.clear()


# ════════════════════════════════════════════════════════════════════
#  MessageValidator — 消息内容校验（方案2：SDK 层软限制）
# ════════════════════════════════════════════════════════════════════

# 默认拒绝的消息模式 — 拒绝明显非协议格式的消息
_BLOCKED_PATTERNS = [
    # Shell 命令
    r"^\s*(?:sudo|bash|sh|curl|wget|chmod|chown|rm\s+-rf|mkfs|dd|:(){)\s",
    # 裸配置命令
    r"^\s*(?:nsc|nkeys|jwt|nats)\s",
    # 纯 JSON 非信封（无 ver/from/id 字段）
    r"^\{\s*\"(?!ver|from|id|type)\w+\":",
    # 二进制/不可打印字符
    # 通过长度检查覆盖
]

# 最大消息长度（字节）— 防止缓冲区溢出
_MAX_MSG_LENGTH = 65536
# 推荐消息长度（超过此值打警告）
_RECOMMENDED_MAX_MSG_LENGTH = 16384


class MessageValidator:
    """消息内容校验器（方案2：SDK 层软限制）

    在 send_dm/send_grp 入口做消息内容白名单校验。
    默认开启，可通过 enable_validation=False 关闭。

    校验规则:
      1. 长度检查：拒绝长于 _MAX_MSG_LENGTH 的消息
      2. 内容类型检查：拒绝明显非协议格式的消息（shell 命令等）
      3. 编码检查：必须可 UTF-8 编码

    用法:
        validator = MessageValidator(strict=True)
        ok, reason = validator.validate("你好呱呱", "dm")
        if not ok:
            log.warning(f"消息被拒绝: {reason}")
    """

    def __init__(self, strict: bool = True):
        self.strict = strict  # True=拒绝不合格消息, False=仅警告
        self.blocked_patterns = [re.compile(p) for p in _BLOCKED_PATTERNS]
        self.stats = {"checked": 0, "passed": 0, "blocked": 0, "warned": 0}

    def validate(self, text: str, msg_type: str) -> Tuple[bool, str]:
        """校验消息内容

        Args:
            text: 消息文本
            msg_type: 消息类型 (dm/grp)

        Returns:
            (True, "") 或 (False, 拒绝原因)
        """
        self.stats["checked"] += 1

        # 1. 编码检查
        try:
            if isinstance(text, str):
                text.encode("utf-8")
        except (UnicodeError, ValueError):
            self.stats["blocked"] += 1
            return False, "消息包含非法编码字符"

        # 2. 长度检查
        byte_len = len(text.encode("utf-8"))
        if byte_len > _MAX_MSG_LENGTH:
            self.stats["blocked"] += 1
            return False, f"消息过长 ({byte_len} bytes > {_MAX_MSG_LENGTH})"

        if byte_len > _RECOMMENDED_MAX_MSG_LENGTH:
            log.warning(f"[Validator] 消息过长 ({byte_len} bytes)，建议缩短")

        # 3. 空消息检查
        if not text or not text.strip():
            self.stats["blocked"] += 1
            return False, "空消息"

        # 4. 模式匹配检查
        for i, pattern in enumerate(self.blocked_patterns):
            if pattern.search(text):
                reason = f"消息包含被禁模式 #{i}: {pattern.pattern[:40]}"
                if self.strict:
                    self.stats["blocked"] += 1
                    return False, reason
                self.stats["warned"] += 1
                log.warning(f"[Validator] {reason} (宽松模式，放行)")
                break

        self.stats["passed"] += 1
        return True, ""

    def get_stats(self) -> dict:
        return {**self.stats}

    def reset_stats(self):
        self.stats = {"checked": 0, "passed": 0, "blocked": 0, "warned": 0}


# ════════════════════════════════════════════════════════════════════
#  AIM NATS 客户端（集成 Pin + RetryManager + MessageValidator）
# ════════════════════════════════════════════════════════════════════

class AIMNATSClient:
    """AIM NATS 客户端（Veritas 协议 v1.0）

    封装 NATS 连接/认证/订阅/发送/JetStream 持久化 + Pin 去重 + RetryManager。
    统一使用 aim. 命名空间。
    """

    def __init__(
        self,
        agent_id: str,
        server: str = "nats://127.0.0.1:4222",
        credentials: str = "",
        pin_ttl: int = 120,
        pin_db_dir: str = "",
        obs_rate_limit: int = 5,
        enable_validation: bool = True,
        validation_strict: bool = True,
    ):
        self.agent_id = agent_id
        self.server = server
        self.credentials = credentials  # 空=裸连, 字符串=token, .creds/.nkey=NKEY/JWT 文件路径

        # 限流器（emit_obs: obs_rate_limit 条/s/agent，可配置）
        self._obs_limiter = RateLimiter(max_per_second=obs_rate_limit)
        self.nc: Optional[NATSClient] = None
        self.js: Optional[JetStreamContext] = None
        self._subscriptions: Dict[str, Any] = {}
        self._running = False
        self._dm_handler: Optional[Callable] = None
        self._grp_handlers: Dict[str, Callable] = {}

        # 集成子组件
        self.pin = AIMPin(agent_id=agent_id, ttl=pin_ttl, db_dir=pin_db_dir)
        self.retry = RetryManager(agent_id=agent_id)

        # 方案2：消息校验器（SDK 层软限制）
        self.enable_validation = enable_validation
        self.validator = MessageValidator(strict=validation_strict)

        # 自动清理 pin 持久化的定时任务
        self._flush_task: Optional[asyncio.Task] = None

    # ── NATS 回调 ────────────────────────────────────────

    async def _on_nats_error(self, e):
        """NATS 连接错误回调"""
        log.warning(f"⚠️ [{self.agent_id}] NATS error: {e}")

    async def _on_nats_disconnected(self):
        """NATS 断连回调"""
        log.warning(f"🔌 [{self.agent_id}] NATS disconnected")

    async def _on_nats_reconnected(self):
        """NATS 重连成功回调"""
        log.info(f"🔄 [{self.agent_id}] NATS reconnected")
        # 重连后重新初始化 JetStream
        try:
            self.js = self.nc.jetstream()
        except Exception:
            pass

    # ── 连接/断开 ─────────────────────────────────────────

    async def connect(self):
        """连接 NATS

        支持自动重连（nats-py 内置指数退避）。

        认证方式（由 credentials 自动识别）:
          - 空字符串 → 裸连（开发调试）
          - Token 字符串 → Token 认证
          - .creds/.nkey 文件 → NKEY/JWT 认证
        """
        kwargs = {
            "servers": [self.server],
            "max_reconnect_attempts": -1,
            "reconnect_time_wait": 2,
            "ping_interval": 30,
            "max_outstanding_pings": 5,
            "name": f"AIM-{self.agent_id}",
            "error_cb": self._on_nats_error,
            "disconnected_cb": self._on_nats_disconnected,
            "reconnected_cb": self._on_nats_reconnected,
        }
        if self.credentials:
            # 自动识别：.creds/.nkey 文件→NKEY/JWT, 否则→Token
            if os.path.isfile(self.credentials):
                kwargs["user_credentials"] = self.credentials
            else:
                kwargs["token"] = self.credentials

        try:
            self.nc = await nats.connect(**kwargs)
        except Exception as e:
            log.error(f"❌ [{self.agent_id}] NATS 连接失败: {e}")
            raise

        self.js = self.nc.jetstream()
        self._running = True
        log.info(f"✅ [{self.agent_id}] NATS connected: {self.server}")

        # 启动定时 flush
        self._flush_task = asyncio.create_task(self._periodic_flush())
        return self

    async def wait_for_connection(self, timeout: float = 10.0):
        """等待连接就绪

        Args:
            timeout: 最大等待秒数

        Raises:
            TimeoutError: 超时连接未就绪
        """
        start = time.time()
        while not self.is_connected:
            if time.time() - start > timeout:
                raise TimeoutError(f"[{self.agent_id}] NATS 连接超时 ({timeout}s)")
            await asyncio.sleep(0.1)

    @classmethod
    def from_config(
        cls,
        agent_id: str,
        config_path: str = "~/.aim/config/aim.json",
        **overrides,
    ):
        """从配置文件创建客户端

        从 aim.json 读取 nats_server / nats_token 自动配置。

        Args:
            agent_id: Agent ID
            config_path: 配置文件路径（默认 ~/.aim/config/aim.json）
            **overrides: 覆盖配置的关键字参数（如 server=..., credentials=...）

        Returns:
            AIMNATSClient 实例

        用法:
            client = AIMNATSClient.from_config("ZS0002")
            client = AIMNATSClient.from_config("ZS0002", server="nats://other:4222")
        """
        import json as _json
        import os as _os

        config_path = _os.path.expanduser(config_path)
        cfg = {}
        if _os.path.exists(config_path):
            with open(config_path) as f:
                cfg = _json.load(f)

        return cls(
            agent_id=agent_id,
            server=overrides.get("server", cfg.get("nats_server", "nats://127.0.0.1:4222")),
            credentials=overrides.get(
                "credentials",
                _resolve_credentials(cfg, agent_id=agent_id),
            ),
            pin_ttl=overrides.get("pin_ttl", 120),
            pin_db_dir=overrides.get("pin_db_dir", ""),
        )

    async def _periodic_flush(self):
        """每 60s 将 pin 内存缓存刷入持久化存储"""
        while self._running:
            await asyncio.sleep(60)
            try:
                await self.pin.flush()
            except Exception:
                pass

    async def disconnect(self):
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None
        try:
            await self.pin.flush()
        except Exception:
            pass
        if self.nc:
            try:
                await self.nc.drain()
                log.info(f"🔌 [{self.agent_id}] disconnected")
            except Exception as e:
                log.debug(f"🔌 [{self.agent_id}] disconnect error: {e}")

    async def close(self):
        """关闭全部订阅并断开"""
        for sub in self._subscriptions.values():
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        self._subscriptions.clear()
        await self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self.nc is not None and self.nc.is_connected

    # ── Agent Card（Phase 0+ Discovery 基础）──

    async def ensure_card_bucket(self):
        """确保 Agent Card 的 KV bucket 存在"""
        from nats.js import JetStreamContext
        try:
            if self.js:
                try:
                    await self.js.key_value(Subjects.card_bucket())
                except Exception:
                    await self.js.create_key_value(
                        bucket=Subjects.card_bucket(),
                        description="Agent Card 注册信息（Discovery）",
                    )
        except Exception as e:
            log.warning(f"[{self.agent_id}] 创建 KV bucket 失败: {e}")

    async def publish_agent_card(self, card: dict) -> bool:
        """发布 Agent Card 到 NATS KV（Discovery 注册 + 更新）

        Agent Card 格式见 v1.2 方案 5.6 Identity。

        Args:
            card: Agent Card dict，必须含 global_id

        Returns:
            True=成功，False=失败
        """
        global_id = card.get("global_id", self.agent_id)
        try:
            await self.ensure_card_bucket()
            kv = await self.js.key_value(Subjects.card_bucket())
            data = json.dumps(card, ensure_ascii=False).encode()
            await kv.put(Subjects.card(global_id), data)

            # 通知其他 Agent：Card 已更新
            event_data = json.dumps({
                "agent_id": self.agent_id,
                "global_id": global_id,
                "event": "updated",
                "ts": time.time(),
            }, ensure_ascii=False).encode()
            await self.nc.publish(Subjects.card_event("updated"), event_data)

            log.info(f"📇 [{self.agent_id}] Agent Card 已发布 (global_id={global_id})")
            return True
        except Exception as e:
            log.warning(f"📇 [{self.agent_id}] Agent Card 发布失败: {e}")
            return False

    async def fetch_agent_card(self, global_id: str) -> dict | None:
        """读取指定 Agent 的 Card"""
        try:
            await self.ensure_card_bucket()
            kv = await self.js.key_value(Subjects.card_bucket())
            entry = await kv.get(Subjects.card(global_id))
            if entry:
                return json.loads(entry.value.decode())
            return None
        except Exception:
            return None

    async def list_agent_cards(self) -> list[dict]:
        """列出所有已注册的 Agent Card（Discovery 在线列表）"""
        try:
            await self.ensure_card_bucket()
            kv = await self.js.key_value(Subjects.card_bucket())
            keys = await kv.keys()
            cards = []
            for key in keys:
                entry = await kv.get(key)
                if entry:
                    try:
                        cards.append(json.loads(entry.value.decode()))
                    except json.JSONDecodeError:
                        pass
            return cards
        except Exception as e:
            log.warning(f"[{self.agent_id}] 列出 Agent Card 失败: {e}")
            return []

    # ── JetStream 流设置 ─────────────────────────────────

    async def setup_streams(self):
        streams = [
            {
                "name": "aim-messages",
                "subjects": ["aim.dm.>", "aim.grp.>"],
                "max_age": 7 * 24 * 3600,  # 7 天 (秒)
                "max_msgs": 100000,
                "max_msg_size": 1 * 1024 * 1024,
                "duplicate_window": 120,
                "storage": "file",
            },
            {
                "name": "aim-observations",
                "subjects": ["aim.obs.>"],
                "max_age": 24 * 3600,  # 1 天 (秒)
                "max_msg_size": 64 * 1024,
                "storage": "file",
            },
            {
                "name": "aim-system",
                "subjects": ["aim.sys.>"],
                "max_age": 30 * 24 * 3600,  # 30 天 (秒)
                "storage": "file",
            },
        ]
        for cfg in streams:
            try:
                existing = await self.js.stream_info(cfg["name"])
                log.info(f"📦 Stream exists: {cfg['name']}")
            except Exception:
                try:
                    sc = StreamConfig(
                        name=cfg["name"],
                        subjects=cfg["subjects"],
                        retention="limits",
                        storage=cfg.get("storage", "file"),
                        max_age=cfg.get("max_age", 7 * 24 * 3600),
                        max_msgs=cfg.get("max_msgs", -1),
                        max_msg_size=cfg.get("max_msg_size", -1),
                        duplicate_window=cfg.get("duplicate_window", 120),
                    )
                    await self.js.add_stream(config=sc)
                    log.info(f"✅ Stream created: {cfg['name']}")
                except Exception as e:
                    log.warning(f"⚠️ Stream create failed {cfg['name']}: {e}")

    async def setup_consumer(self, stream: str = "aim-messages", durable: str = ""):
        durable_name = durable or f"agent-{self.agent_id}"
        try:
            existing = await self.js.consumer_info(stream, durable_name)
            log.info(f"👤 Consumer exists: {durable_name}")
        except Exception:
            try:
                # 订阅 DM 和群聊消息
                filter_subjects = [
                    Subjects.dm(self.agent_id),  # aim.dm.ZS0003
                    Subjects.grp("grp_trio"),     # aim.grp.grp_trio
                ]
                await self.js.add_consumer(
                    stream,
                    durable_name=durable_name,
                    deliver_policy="all",
                    ack_policy="explicit",
                    max_deliver=5,
                    ack_wait=30,
                    filter_subjects=filter_subjects,
                )
                log.info(f"✅ Consumer created: {durable_name}")
            except Exception as e:
                log.warning(f"⚠️ Consumer create failed {durable_name}: {e}")

    # ── 订阅（集成 Pin 去重） ────────────────────────────

    def _wrap_coro(self, handler: Callable):
        """将 handler 包装为 coroutine，自动去重"""
        async def _cb(msg):
            try:
                envelope = parse_message(msg.data)
                msg_id = envelope.get("id", "")
                # Pin 去重：如果已处理过则跳过
                if msg_id and await self.pin.is_duplicate(msg_id):
                    log.debug(f"⏭️ [{self.agent_id}] duplicate msg skipped: {msg_id}")
                    return
                await handler(envelope, msg)
                # 标记已处理
                if msg_id:
                    await self.pin.mark(msg_id)
            except Exception as e:
                log.error(f"Handler error: {e}")
        return _cb

    async def subscribe_dm(self, handler: Callable):
        subject = Subjects.dm(self.agent_id)
        sub = await self.nc.subscribe(subject, cb=self._wrap_coro(handler))
        self._subscriptions[subject] = sub
        self._dm_handler = handler
        log.info(f"📩 [{self.agent_id}] subscribed DM: {subject}")

    async def subscribe_grp(self, group_id: str, handler: Callable):
        subject = Subjects.grp(group_id)
        sub = await self.nc.subscribe(subject, cb=self._wrap_coro(handler))
        self._subscriptions[subject] = sub
        self._grp_handlers[group_id] = handler
        log.info(f"📩 [{self.agent_id}] subscribed group: {subject}")

    async def subscribe_obs(self, handler: Callable, agent_id: str = ">"):
        subject = f"aim.obs.{agent_id}"
        sub = await self.nc.subscribe(subject, cb=self._wrap_coro(handler))
        self._subscriptions[subject] = sub
        log.info(f"👁️ [{self.agent_id}] subscribed observer: {subject}")

    async def subscribe_sys(self, handler: Callable):
        sub = await self.nc.subscribe(Subjects.sys_all(), cb=self._wrap_coro(handler))
        self._subscriptions[Subjects.sys_all()] = sub
        log.info(f"📡 [{self.agent_id}] subscribed system events")

    # ── 发送（支持重试） ─────────────────────────────────

    async def send_dm(
        self,
        to_id: str,
        text: str,
        reply_to: str = "",
        use_jetstream: bool = False,
        enable_retry: bool = True,
    ) -> Dict[str, Any]:
        """发送私聊消息（可选重试 + 可配置消息校验）"""
        # 方案2：消息内容校验
        if self.enable_validation:
            ok, reason = self.validator.validate(text, "dm")
            if not ok:
                log.warning(f"⛔ [{self.agent_id}] 消息被校验拒绝 (DM → {to_id}): {reason}")
                raise ValueError(f"消息校验未通过: {reason}")

        envelope = make_envelope(
            from_id=self.agent_id, msg_type="dm",
            payload={"text": text}, reply_to=reply_to,
        )
        subject = Subjects.dm(to_id)
        data = json.dumps(envelope, ensure_ascii=False).encode()

        async def _publish():
            if use_jetstream and self.js:
                headers = {"Nats-Msg-Id": envelope["id"]}
                ack = await self.js.publish(subject, data, headers=headers)
                log.info(f"📤 [{self.agent_id}] JS DM → {to_id}: {envelope['id']} seq={ack.seq}")
            else:
                await self.nc.publish(subject, data)
                log.info(f"📤 [{self.agent_id}] DM → {to_id}: {envelope['id']}")
            return envelope

        if enable_retry:
            ok, result = await self.retry.retry_send(
                label=f"send_dm_{to_id}",
                fn=_publish,
            )
            if ok:
                return result or envelope
            raise RuntimeError(f"Failed to send DM to {to_id}: {result}")
        else:
            await _publish()
            return envelope

    async def send_grp(self, group_id: str, text: str, enable_retry: bool = True) -> Dict[str, Any]:
        """发送群聊消息（可选重试 + 可配置消息校验）"""
        # 方案2：消息内容校验
        if self.enable_validation:
            ok, reason = self.validator.validate(text, "grp")
            if not ok:
                log.warning(f"⛔ [{self.agent_id}] 消息被校验拒绝 (GRP → {group_id}): {reason}")
                raise ValueError(f"消息校验未通过: {reason}")

        envelope = make_envelope(
            from_id=self.agent_id, msg_type="grp",
            payload={"text": text},
        )
        subject = Subjects.grp(group_id)
        data = json.dumps(envelope, ensure_ascii=False).encode()

        async def _publish():
            await self.nc.publish(subject, data)
            log.info(f"📤 [{self.agent_id}] group → {group_id}: {envelope['id']}")
            return envelope

        if enable_retry:
            ok, result = await self.retry.retry_send(
                label=f"send_grp_{group_id}",
                fn=_publish,
            )
            if ok:
                return result or envelope
            raise RuntimeError(f"Failed to send group to {group_id}: {result}")
        else:
            await _publish()
            return envelope

    async def send_request(
        self, to_id: str, text: str, timeout: float = 5.0, enable_retry: bool = True
    ) -> Dict[str, Any]:
        """请求-回复模式（可选重试）"""
        envelope = make_envelope(
            from_id=self.agent_id, msg_type="request",
            payload={"text": text},
        )
        subject = Subjects.dm(to_id)
        data = json.dumps(envelope, ensure_ascii=False).encode()

        async def _request():
            response = await self.nc.request(subject, data, timeout=timeout)
            return parse_message(response.data)

        if enable_retry:
            ok, result = await self.retry.retry_request(
                label=f"req_{to_id}",
                fn=_request,
            )
            if ok:
                return result
            raise RuntimeError(f"Request to {to_id} failed: {result}")
        else:
            return await _request()

    # ── Observer 和系统事件 ──────────────────────────────

    def _check_obs_rate(self) -> bool:
        """emit_obs 限流检测：通过 _obs_limiter"""
        return self._obs_limiter.check(self.agent_id)

    async def emit_state_report(self,
                                  status: str,
                                  active_sessions: int = 0,
                                  queue_depth: int = 0,
                                  avg_latency_ms: float = 0.0,
                                  msg_id: str = "",
                                  detail: str = "",
                                  use_jetstream: bool = True):
        """发布 StateReport 格式的 Observer 事件（Phase 0 标准格式）

        StateReport 包含 Monitor 级别的 Runtime 健康状态信息。

        Args:
            status: healthy / degraded / unhealthy / received / processing / completed / error / timeout / heartbeat
            active_sessions: Runtime 当前活跃 session 数
            queue_depth: pending 队列深度
            avg_latency_ms: 平均处理延迟
            msg_id: 关联消息 ID
            detail: 人类可读描述
            use_jetstream: 是否同时写入 JetStream
        """
        # 限流检测
        if not self._check_obs_rate():
            log.debug(f"[{self.agent_id}] emit_state_report rate limited: {status}")
            return

        event = {
            "agent_id": self.agent_id,
            "status": status,
            "active_sessions": active_sessions,
            "queue_depth": queue_depth,
            "avg_latency_ms": avg_latency_ms,
            "msg_id": msg_id,
            "detail": detail,
            "ts": time.time(),
            "nonce": uuid.uuid4().hex[:12],
        }
        subject = Subjects.obs(self.agent_id)
        data = json.dumps(event, ensure_ascii=False).encode()

        js_ok = False
        if use_jetstream and self.js:
            try:
                headers = {"Nats-Msg-Id": f"obs-{self.agent_id}-{status}-{int(time.time()*1000)}"}
                ack = await self.js.publish(subject, data, headers=headers)
                js_ok = True
            except Exception as e:
                log.debug(f"emit_state_report JS publish failed: {e}")

        # raw NATS publish（实时，observer/aim-watch 订阅用）
        try:
            await self.nc.publish(subject, data)
        except Exception as e:
            log.debug(f"emit_state_report raw publish failed: {e}")

        return js_ok

    async def emit_obs(self, status: str, msg_id: str = "", detail: str = "",
                       use_jetstream: bool = True):
        """发布 Observer 状态事件（支持限流 + JetStream 持久化）

        自动使用 JetStream publish（持久化到 aim-observations stream），
        确保 aim-watch --history 可回放历史记录。

        限流：默认 5条/s，超出直接丢弃（日志 debug）。
        """
        # 限流检测
        if not self._check_obs_rate():
            log.debug(f"[{self.agent_id}] emit_obs rate limited ({self._obs_limiter.max_per_second}/s): {status}")
            return

        event = {
            "agent_id": self.agent_id,
            "status": status,
            "msg_id": msg_id,
            "detail": detail,
            "ts": time.time(),
            "nonce": uuid.uuid4().hex[:12],  # 防重放
        }
        subject = Subjects.obs(self.agent_id)
        data = json.dumps(event, ensure_ascii=False).encode()

        # 双发策略：JS 持久化（历史回放） + raw 实时（observer/aim-watch 订阅）
        js_ok = False
        if use_jetstream and self.js:
            try:
                headers = {"Nats-Msg-Id": f"obs-{self.agent_id}-{status}-{int(time.time()*1000)}"}
                ack = await self.js.publish(subject, data, headers=headers)
                js_ok = True
                log.info(f"[{self.agent_id}] JS obs ack: seq={ack.stream}/{ack.seq} status={status}")
            except Exception as e:
                log.warning(f"[{self.agent_id}] JS obs publish failed ({status}): {e}, fallback to raw")

        # raw publish 确保实时订阅者（observer/aim-watch）能收到
        # 无论 JS 是否成功都发 raw publish（双发策略）
        if not use_jetstream:
            await self.nc.publish(subject, data)
        else:
            # JS 成功时也发一次 raw，保证 core NATS subscriber（Observer）实时性
            try:
                await self.nc.publish(subject, data)
            except Exception as e:
                log.debug(f"[{self.agent_id}] raw obs publish failed: {e}")

    async def start_heartbeat(self, interval: float = 30.0):
        """启动定时心跳 Observer（每 interval 秒推送 heartbeat 状态）

        让 aim-watch 能实时感知 Agent 是否存活。
        """
        async def _heartbeat_loop():
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self.emit_obs("heartbeat", detail="alive")
                except Exception as e:
                    log.debug(f"[{self.agent_id}] heartbeat failed: {e}")
        self._heartbeat_task = asyncio.create_task(_heartbeat_loop())
        log.info(f"💓 [{self.agent_id}] heartbeat started (interval={interval}s)")

    async def stop_heartbeat(self):
        """停止心跳 Observer"""
        if hasattr(self, '_heartbeat_task') and self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def publish_sys(self, event_type: str, data: Dict[str, Any]):
        event = {"type": event_type, "ts": time.time(), "data": data}
        subject = Subjects.sys_event(event_type)
        await self.nc.publish(subject, json.dumps(event, ensure_ascii=False).encode())

    # ── aim_observe — 临时观察 Agent 状态 ────────────────

    async def aim_observe(
        self,
        target_id: str,
        timeout: float = 10.0,
        expected_event: str = "",
    ) -> List[Dict[str, Any]]:
        """临时观察目标 Agent 的状态事件（返回 timeout 内收集的事件）

        适合一对一的"看一看当前状态"场景。
        持续的 watch 用 aim_watch()。

        Args:
            target_id: 目标 Agent ID（如 "ZS0001"）
            timeout: 等待时长（秒），默认 10s
            expected_event: 可选，只关注特定状态类型

        Returns:
            收集到的事件列表（按时间排序）
        """
        events: List[Dict[str, Any]] = []
        stop_event = asyncio.Event()

        async def _obs_handler(envelope: dict, msg):
            payload = envelope if "status" in envelope else envelope.get("payload", envelope)
            if expected_event and payload.get("status") != expected_event:
                return
            events.append(payload)
            # 如果找到了期望的事件类型，提前结束
            if expected_event:
                stop_event.set()

        subject = f"aim.obs.{target_id}"
        sub = await self.nc.subscribe(subject, cb=self._wrap_coro(_obs_handler))
        log.info(f"🔭 [{self.agent_id}] observe {target_id} (timeout={timeout}s)")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            await sub.unsubscribe()

        log.info(f"🔭 [{self.agent_id}] observe {target_id}: collected {len(events)} events")
        return events

    # ── aim_watch — 持续观察模式 ────────────────────────

    class WatchSession:
        """一个持续观察会话

        由 aim_watch() 返回，可在外部调用 stop() 停止。
        """

        def __init__(self, subject: str, worker: asyncio.Task):
            self.subject = subject
            self._worker = worker
            self._stopped = False

        async def stop(self):
            if not self._stopped:
                self._stopped = True
                self._worker.cancel()
                try:
                    await self._worker
                except asyncio.CancelledError:
                    pass

        @property
        def running(self) -> bool:
            return not self._stopped and not self._worker.done()

    async def aim_watch(
        self,
        predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
        callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        target_id: str = ">",
    ) -> "WatchSession":
        """启动持续观察模式，匹配的事件触发 callback

        Args:
            predicate: 过滤函数，接收事件 dict，返回 True 才触发
            callback: 事件处理函数（async），接收匹配的事件
            target_id: 目标 Agent ID 或 ">"（所有 Agent），默认 ">"

        Returns:
            WatchSession 对象，可调用 .stop() 停止
        """
        if callback is None:
            # 默认回调只打日志
            async def _default_cb(event):
                log.info(f"👁️ [{self.agent_id}] watch event: {event.get('status', '?')} from {event.get('agent_id', '?')}")
            callback = _default_cb

        async def _watch_loop():
            events: List[Dict] = []

            async def _obs_handler(envelope: dict, msg):
                payload = envelope if "status" in envelope else envelope.get("payload", envelope)
                if predicate and not predicate(payload):
                    return
                events.append(payload)
                try:
                    await callback(payload)
                except Exception as e:
                    log.error(f"watch callback error: {e}")

            subject = f"aim.obs.{target_id}"
            sub = await self.nc.subscribe(subject, cb=self._wrap_coro(_obs_handler))
            log.info(f"👁️ [{self.agent_id}] watching: {subject}")

            try:
                # 一直运行直到被取消
                while self._running:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    await sub.unsubscribe()
                except Exception:
                    pass
                log.info(f"👁️ [{self.agent_id}] watch stopped: {subject}")

        worker = asyncio.create_task(_watch_loop())
        return self.WatchSession(subject=f"aim.obs.{target_id}", worker=worker)

    # ── aim_list — 拉取历史消息 ─────────────────────────

    async def aim_list(
        self,
        target_id: str = "",
        limit: int = 20,
        stream: str = "",
        subject_filter: str = "",
    ) -> List[Dict[str, Any]]:
        """从 JetStream 拉取历史消息

        目标 Agent 最近的消息（DM / 群聊 / 观察事件）。

        Args:
            target_id: Agent ID，留空则从当前 Agent 拉
            limit: 最大返回条数（默认 20，最大 100）
            stream: JetStream Stream 名，自动推导
                - "messages": DM/群聊历史
                - "observations": 观察事件历史
                - 留空：自动用 aim-messages
            subject_filter: 可选精确 subject 过滤

        Returns:
            消息列表（按时间从新到旧）
        """
        if not self.js:
            raise RuntimeError("JetStream not available (not connected)")

        stream_name = stream or "aim-messages"
        target = target_id or self.agent_id

        # 推导过滤 subject
        if not subject_filter:
            if stream_name == "aim-observations":
                subject_filter = f"aim.obs.{target}"
            else:
                subject_filter = f"aim.dm.{target}"

        log.info(f"📋 [{self.agent_id}] listing {stream_name}: {subject_filter} (limit={limit})")

        try:
            # 创建临时 Consumer 获取历史
            consumer_name = f"hist-{self.agent_id}-{uuid.uuid4().hex[:6]}"
            consumer = await self.js.add_consumer(
                stream_name,
                durable_name=consumer_name,
                deliver_policy="all",
                ack_policy="all",
                max_deliver=1,
                filter_subjects=[subject_filter],
                inactive_threshold=10,
            )

            messages: List[Dict] = []
            try:
                sub = await self.js.subscribe(subject_filter, durable=consumer_name)
                async for msg in sub.messages:
                    try:
                        envelope = parse_message(msg.data)
                        messages.append(envelope)
                        await msg.ack()
                    except Exception:
                        await msg.term()
                    if len(messages) >= min(limit, 100):
                        break
                    # 10 消息后尝试 break（如果没更多消息了）
                    if len(messages) >= limit:
                        break
            except Exception:
                pass
            finally:
                try:
                    await self.js.delete_consumer(stream_name, consumer_name)
                except Exception:
                    pass

            # 从新到旧排序
            messages.sort(key=lambda m: m.get("ts", ""), reverse=True)
            log.info(f"📋 [{self.agent_id}] list: got {len(messages)} messages from {subject_filter}")
            return messages[:limit]

        except Exception as e:
            log.warning(f"📋 list failed (stream not ready?): {e}")
            return []

    # ── Observer 骨架 ────────────────────────────────────

    class ObserverSkeleton:
        """Observer 骨架 — 高级观察器

        在 subscribe_obs 基础上封装：
          - 类型过滤（message / system / status）
          - 事件缓冲 / 批处理
          - 报告生成
          - 观察会话管理

        Usage:
            obs = AIMNATSClient.ObserverSkeleton(client)
            obs.add_filter("status", ["heartbeat", "error"])
            obs.on_event(my_callback)
            await obs.start()

            # 获取汇总报告
            report = obs.report()
        """

        EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]

        def __init__(self, client: "AIMNATSClient"):
            self.client = client
            self._filters: Dict[str, List[str]] = {}  # type -> [values]
            self._callbacks: List[self.EventCallback] = []
            self._buffer: List[Dict[str, Any]] = []
            self._buffer_lock = asyncio.Lock()
            self._running = False
            self._session = None
            self._started_at: float = 0.0
            self._event_counts: Dict[str, int] = {}

        def add_filter(self, field: str, values: List[str]):
            """添加过滤器：只关注指定字段的特定值

            Args:
                field: 事件字段名（如 "status", "type"）
                values: 允许的值列表（如 ["heartbeat", "error"]）
            """
            self._filters[field] = values

        def clear_filters(self):
            self._filters.clear()

        def on_event(self, callback: EventCallback):
            """注册事件回调（async）"""
            self._callbacks.append(callback)

        async def start(self):
            """启动 Observer"""
            if self._running:
                log.warning("Observer already running")
                return

            self._running = True
            self._started_at = time.time()
            self._buffer.clear()
            self._event_counts.clear()

            async def _filter_cb(event: Dict[str, Any]):
                # 应用过滤器
                for field, allowed in self._filters.items():
                    val = event.get(field, "")
                    if val not in allowed:
                        return

                # 计数
                status = event.get("status", "unknown")
                self._event_counts[status] = self._event_counts.get(status, 0) + 1

                # 缓冲
                async with self._buffer_lock:
                    self._buffer.append(event)
                    if len(self._buffer) > 1000:
                        self._buffer = self._buffer[-500:]

                # 回调
                for cb in self._callbacks:
                    try:
                        await cb(event)
                    except Exception as e:
                        log.error(f"Observer callback error: {e}")

            self._session = await self.client.aim_watch(
                predicate=None,
                callback=_filter_cb,
                target_id=">",
            )
            log.info("🔭 Observer started")

        async def stop(self):
            """停止 Observer"""
            self._running = False
            if self._session:
                await self._session.stop()
                self._session = None
            log.info("🔭 Observer stopped")

        async def flush_buffer(self) -> List[Dict[str, Any]]:
            """清空并返回缓冲的事件列表"""
            async with self._buffer_lock:
                events = list(self._buffer)
                self._buffer.clear()
            return events

        def report(self) -> Dict[str, Any]:
            """生成观察报告"""
            elapsed = time.time() - self._started_at if self._started_at else 0
            return {
                "agent_id": self.client.agent_id,
                "running": self._running,
                "elapsed_seconds": round(elapsed, 1),
                "event_counts": dict(self._event_counts),
                "total_events": sum(self._event_counts.values()),
                "buffered": len(self._buffer),
                "filters": dict(self._filters),
            }

    # ── JetStream 持久化消费 ─────────────────────────────

    async def consume_jetstream(self, handler: Callable, durable: str = ""):
        """通过 JetStream Durable Consumer 消费消息（带 Pin 去重）"""
        durable_name = durable or f"agent-{self.agent_id}"
        subject = Subjects.dm(self.agent_id)

        sub = await self.js.subscribe(subject, durable=durable_name)
        async for msg in sub.messages:
            try:
                envelope = parse_message(msg.data)
                msg_id = envelope.get("id", "")
                # Pin 去重
                if msg_id and await self.pin.is_duplicate(msg_id):
                    log.debug(f"⏭️ [{self.agent_id}] JS duplicate: {msg_id}")
                    await msg.ack()
                    continue
                await handler(envelope, msg)
                if msg_id:
                    await self.pin.mark(msg_id)
                await msg.ack()
            except Exception as e:
                log.error(f"JS handler error: {e}")
                await msg.nak()

    # ── 工具 ─────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "server": self.server,
            "connected": self.is_connected,
            "subscriptions": list(self._subscriptions.keys()),
            "pin": self.pin.get_stats(),
            "retry": self.retry.get_stats(),
        }


# ════════════════════════════════════════════════════════════════════
#  AIM Observer 客户端（只读）
# ════════════════════════════════════════════════════════════════════

class AIMObserverClient:
    """AIM Observer — 只读观察者

    不注册为 Agent，不发布任何消息，只订阅 aim.obs.> 接收状态事件。
    适用于 aim-watch、监控面板等只读场景。

    用法:
        observer = AIMObserverClient(credentials=token)
        await observer.connect()
        await observer.subscribe(lambda e: print(e.status))
        await observer.wait_forever()
    """

    def __init__(
        self,
        observer_id: str = "observer",
        server: str = "nats://127.0.0.1:4222",
        credentials: str = "",
        num_workers: int = 1,
    ):
        self.observer_id = observer_id
        self.server = server
        self.credentials = credentials
        self.num_workers = num_workers
        self.nc: Optional[NATSClient] = None
        self.js: Optional[JetStreamContext] = None
        self._handler: Optional[Callable] = None
        self._running = False
        self._worker_tasks: List[asyncio.Task] = []
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        """连接 NATS（只订阅，不注册）"""
        kwargs = {
            "servers": [self.server],
            "max_reconnect_attempts": -1,
            "reconnect_time_wait": 2,
            "ping_interval": 30,
            "max_outstanding_pings": 5,
            "name": f"OBS-{self.observer_id}",
        }
        if self.credentials:
            if os.path.isfile(self.credentials):
                kwargs["user_credentials"] = self.credentials
            else:
                kwargs["token"] = self.credentials
        self.nc = await nats.connect(**kwargs)
        self.js = self.nc.jetstream()
        self._running = True
        log.info(f"👁️  [{self.observer_id}] Observer connected: {self.server}")
        return self

    async def subscribe(self, handler: Callable, agent_filter: str = ">"):
        """订阅 Observer 事件（支持 worker 池并行处理）

        Args:
            handler: 回调函数 async def handler(event: dict)
            agent_filter: Agent 过滤，">"=全部, "ZS0001"=只看某个
        """
        self._handler = handler
        subject = f"aim.obs.{agent_filter}"

        async def _on_event(msg):
            try:
                event = parse_message(msg.data)
                await self._event_queue.put(event)
            except Exception as e:
                log.debug(f"[{self.observer_id}] parse error: {e}")

        # 启动 worker 池
        for i in range(self.num_workers):
            task = asyncio.create_task(self._worker_loop(i))
            self._worker_tasks.append(task)

        await self.nc.subscribe(subject, cb=_on_event)
        log.info(f"   📡 subscribed: {subject} (workers={self.num_workers})")
        return self

    async def _worker_loop(self, worker_id: int):
        """Worker 循环：从队列取事件并调用 handler"""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                if self._handler:
                    try:
                        await self._handler(event)
                    except Exception as e:
                        log.error(f"[{self.observer_id}] worker#{worker_id} handler error: {e}")
            except asyncio.TimeoutError:
                continue

    async def get_history(
        self,
        agent_filter: str = ">",
        start_time: float = 0,
        end_time: float = 0,
        page: int = 1,
        page_size: int = 20,
    ) -> list:
        """从 JetStream 分页查询历史 Observer 事件

        Args:
            agent_filter: Agent 过滤
            start_time: 起始时间戳（0=不限）
            end_time: 结束时间戳（0=不限）
            page: 页码（从 1 开始）
            page_size: 每页条数

        Returns:
            事件列表（按时间排序）
        """
        if not self.js:
            log.warning(f"[{self.observer_id}] JetStream 不可用")
            return []

        subject = f"aim.obs.{agent_filter}"
        events = []

        try:
            opts: dict = {"max_messages": page_size}
            if start_time > 0:
                opts["opt_start_time"] = datetime.fromtimestamp(start_time, tz=timezone.utc)
            if end_time > 0:
                opts["opt_end_time"] = datetime.fromtimestamp(end_time, tz=timezone.utc)

            # JetStream 分页订阅
            sub = await self.js.subscribe(
                subject=subject,
                stream="aim-observations",
                deliver_policy="all",
            )

            collected = 0
            skip = (page - 1) * page_size
            while collected < page_size:
                try:
                    msg = await sub.next_msg(timeout=2)
                    if skip > 0:
                        skip -= 1
                        continue
                    event = parse_message(msg.data)
                    events.append(event)
                    collected += 1
                except (asyncio.TimeoutError, nats.errors.TimeoutError):
                    break

            await sub.unsubscribe()
        except Exception as e:
            log.debug(f"[{self.observer_id}] history query failed: {e}")

        return events

    async def wait_forever(self):
        """永久运行，直到 Ctrl+C"""
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            await self.disconnect()

    async def disconnect(self):
        self._running = False
        # 取消所有 worker
        for task in self._worker_tasks:
            task.cancel()
        self._worker_tasks.clear()
        if self.nc:
            await self.nc.close()

    @property
    def is_connected(self) -> bool:
        return self.nc is not None and self.nc.is_connected

    @classmethod
    def from_config(
        cls,
        observer_id: str = "observer",
        server: str = "nats://127.0.0.1:4222",
        config_path: str = "~/.aim/config/aim.json",
    ):
        """从配置文件创建 Observer 客户端"""
        import json as _json
        import os as _os
        config_path = _os.path.expanduser(config_path)
        cfg = {}
        if _os.path.exists(config_path):
            with open(config_path) as f:
                cfg = _json.load(f)
        return cls(
            observer_id=observer_id,
            server=cfg.get("nats_server", server),
            credentials=_resolve_credentials(cfg),
        )
