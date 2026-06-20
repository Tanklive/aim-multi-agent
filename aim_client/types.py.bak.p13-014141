"""AIM Client — Phase 0 共享类型定义"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time

class AgentState(Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


class DegradeLevel(Enum):
    """三级降级模型 — 系统健康程度

    L0 NORMAL:   Runtime 空闲，消息即时处理。dispatch 成功。
    L1 DEGRADED: Runtime 暂时忙（TUI 占 session / 排队中）。
                 消息 pending 排队，不丢。探针确认空闲后自动恢复。
    L2 STALLED:  Runtime 不可用（lettacron 挂了 / Node.js 崩了）。
                 消息持久化 dead 队列，定期探针等待恢复。
    """
    L0 = "normal"
    L1 = "degraded"
    L2 = "stalled"

class DeliveryMode(Enum):
    REALTIME = "realtime"
    DEFERRED = "deferred"
    BATCH = "batch"

class AdapterStatus(Enum):
    SUCCESS = 0
    RETRY = 1
    DEGRADE = 2
    HUMAN = 3

class TaskStatus(Enum):
    """Task Contract — 任务生命周期状态"""
    PENDING = "pending"
    NEGOTIATING = "negotiating"    # 协商标 acceptance/rejection
    ASSIGNED = "assigned"           # 已指派执行者
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESULT_PENDING = "result_pending"  # 结果待确认

@dataclass
class StateReport:
    """Monitor 输出的 Runtime 健康状态，Scheduler 只读这个"""
    status: AgentState = AgentState.IDLE
    degrade_level: "DegradeLevel" = None  # L0/L1/L2，Phase 1 新增
    active_sessions: int = 0
    queue_depth: int = 0
    pending_count: int = 0                 # pending 队列深度
    dead_count: int = 0                    # dead 队列深度
    avg_latency_ms: int = 0
    consecutive_failures: int = 0          # 连续 dispatch 失败次数
    last_success_ts: float = 0.0           # 上次 dispatch 成功时间戳
    last_heartbeat: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.degrade_level is None:
            self.degrade_level = DegradeLevel.L0

@dataclass
class Message:
    """AIM 消息信封"""
    msg_id: str
    from_id: str
    to_id: str = ""
    grp_id: str = ""
    msg_type: str = "dm"  # dm | grp
    content: str = ""
    raw_envelope: dict = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)
    dequeued_at: float = 0.0
    retry_count: int = 0

@dataclass
class AIMChat:
    """即时对话 — 无状态，发完即完
    Phase 1 引入：在 Message 信封上叠加语义层
    Transport 层直接投递，Scheduler 不做状态追踪"""
    content: str
    from_id: str
    reply_to: str | None = None       # 回复链 msg_id


@dataclass
class AIMTask:
    """工作指令 — 有状态，需要生命周期追踪
    Phase 1 引入：Scheduler 根据 execution_model 选择投递策略
    Phase 2+ 完整 Task Contract 落地"""
    task_id: str                       # 全局唯一任务 ID
    type: str                          # log-analysis | code-review | research | ...
    input: dict = field(default_factory=dict)  # 任务输入
    owner: str = ""                    # 发任务的人 (agent_id)
    executor: str = ""                 # 执行的人 (agent_id)
    status: TaskStatus = TaskStatus.PENDING
    deadline: str | None = None        # ISO 8601 截止时间
    expect: dict | None = None         # 期望输出格式 {"format":"md","fields":["..."]}


@dataclass
class AgentCard:
    """Agent Card Schema v1 — 完整版

    对应 docs/agent-card-schema-v1.md
    Agent 数字身份证，Registry 注册时写入 KV，其他 Agent 通过 Discovery 读取。
    """
    # ── 身份 ──
    global_id: str = ""
    serial: str = ""
    name: str = ""

    # ── Client & Runtime ──
    client_type: str = "aim-client"
    client_version: str = ""
    runtime_provider: str = ""
    runtime_version: str = ""

    # ── 网络 ──
    endpoint: str = ""
    alt_endpoints: list[str] = field(default_factory=list)
    reachable_from: list[str] = field(default_factory=lambda: ["local"])
    requires_relay: bool = False
    preferred_transport: str = "nats"

    # ── 投递 ──
    delivery_mode: str = "deferred"
    expects_reply: bool = True
    max_concurrency: int = 1
    queue_capacity: int = 1000

    # ── 执行 & 生命周期 ──
    execution_model: str = "deferred"
    lifecycle: str = "AVAILABLE"

    # ── 协议 ──
    protocol_version: str = "1.0"
    min_protocol_version: str = "0.8"

    # ── 能力 ──
    capabilities: list[dict] = field(default_factory=lambda: [{"name": "chat", "version": "1.0", "level": "native"}])

    # ── 信任 & 钱包（P2+ 预留）─
    trust_citizenship: str = "L2"
    trust_reputation: float = 0.0
    trust_completed_tasks: int = 0
    trust_success_rate: float = 0.0
    trust_endorsements: int = 0
    wallet_address: str = ""
    wallet_balance: int = 0
    wallet_stake: int = 0

    def to_dict(self) -> dict:
        """序列化为 JSON schema 格式"""
        return {
            "global_id": self.global_id,
            "serial": self.serial,
            "name": self.name,
            "client": {"type": self.client_type, "version": self.client_version},
            "runtime": {"provider": self.runtime_provider, "version": self.runtime_version},
            "network": {
                "endpoint": self.endpoint,
                "alt_endpoints": self.alt_endpoints,
                "reachable_from": self.reachable_from,
                "requires_relay": self.requires_relay,
                "preferred_transport": self.preferred_transport,
            },
            "delivery": {
                "mode": self.delivery_mode,
                "expects_reply": self.expects_reply,
                "max_concurrency": self.max_concurrency,
                "queue_capacity": self.queue_capacity,
            },
            "execution_model": self.execution_model,
            "lifecycle": self.lifecycle,
            "protocol_version": self.protocol_version,
            "min_protocol_version": self.min_protocol_version,
            "capabilities": self.capabilities,
            "trust": {
                "citizenship": self.trust_citizenship,
                "reputation": self.trust_reputation,
                "completed_tasks": self.trust_completed_tasks,
                "success_rate": self.trust_success_rate,
                "endorsements": self.trust_endorsements,
            },
            "wallet": {
                "address": self.wallet_address,
                "balance": self.wallet_balance,
                "stake": self.wallet_stake,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentCard":
        """从 JSON dict 反序列化"""
        net = data.get("network", {})
        deli = data.get("delivery", {})
        client = data.get("client", {})
        runtime = data.get("runtime", {})
        trust = data.get("trust", {})
        wallet = data.get("wallet", {})
        caps = data.get("capabilities", [{"name": "chat", "version": "1.0", "level": "native"}])
        return cls(
            global_id=data.get("global_id", ""),
            serial=data.get("serial", ""),
            name=data.get("name", ""),
            client_type=client.get("type", "aim-client"),
            client_version=client.get("version", ""),
            runtime_provider=runtime.get("provider", ""),
            runtime_version=runtime.get("version", ""),
            endpoint=net.get("endpoint", ""),
            alt_endpoints=net.get("alt_endpoints", []),
            reachable_from=net.get("reachable_from", ["local"]),
            requires_relay=net.get("requires_relay", False),
            preferred_transport=net.get("preferred_transport", "nats"),
            delivery_mode=deli.get("mode", "deferred"),
            expects_reply=deli.get("expects_reply", True),
            max_concurrency=deli.get("max_concurrency", 1),
            queue_capacity=deli.get("queue_capacity", 1000),
            execution_model=data.get("execution_model", "deferred"),
            lifecycle=data.get("lifecycle", "AVAILABLE"),
            protocol_version=data.get("protocol_version", "1.0"),
            min_protocol_version=data.get("min_protocol_version", "0.8"),
            capabilities=caps,
            trust_citizenship=trust.get("citizenship", "L2"),
            trust_reputation=trust.get("reputation", 0.0),
            trust_completed_tasks=trust.get("completed_tasks", 0),
            trust_success_rate=trust.get("success_rate", 0.0),
            trust_endorsements=trust.get("endorsements", 0),
            wallet_address=wallet.get("address", ""),
            wallet_balance=wallet.get("balance", 0),
            wallet_stake=wallet.get("stake", 0),
        )

@dataclass
class AdapterInfo:
    """adapter.sh info 返回的 Runtime 元信息"""
    provider: str = "unknown"
    version: str = "0.0.0"
    execution_model: str = "deferred"
    max_concurrency: int = 1
    supports_streaming: bool = False


# ── 降级判定函数（Phase 1 P0，纯逻辑，无外部依赖）─────────────


def evaluate_degrade_level(
    health_exit_code: int,
    consecutive_timeouts: int,
    consecutive_health_fails: int,
    current_level: DegradeLevel,
    *,
    l1_trigger_timeouts: int = 3,
    l2_trigger_health_fails: int = 3,
) -> tuple[DegradeLevel, str]:
    """根据 health + dispatch 结果判定降级级别。

    Args:
        health_exit_code: adapter.sh health 本次退出码
        consecutive_timeouts: 连续 dispatch 超时次数
        consecutive_health_fails: 连续 health 失败次数
        current_level: 当前降级级别
        l1_trigger_timeouts: 触发 L1 的连续超时阈值
        l2_trigger_health_fails: 触发 L2 的连续 health 失败阈值

    Returns:
        (new_level, reason) — 新级别和变更原因
    """
    # ── L2 → L0 恢复 ──
    if current_level == DegradeLevel.L2:
        if health_exit_code == 0:
            return (DegradeLevel.L0, "health 恢复，从 L2 升级到 L0")
        return (DegradeLevel.L2, "health 仍不可用，保持 L2")

    # ── L2 触发 ──
    if health_exit_code == 2 or consecutive_health_fails >= l2_trigger_health_fails:
        return (DegradeLevel.L2, f"health 连续 {consecutive_health_fails} 次失败，降级到 L2")

    # ── L1 → L0 恢复 ──
    if current_level == DegradeLevel.L1:
        if health_exit_code == 0 and consecutive_timeouts == 0:
            return (DegradeLevel.L0, "dispatch 恢复，从 L1 升级到 L0")
        if health_exit_code == 0:
            return (DegradeLevel.L1, "health ok 但 dispatch 仍不稳定，保持 L1")

    # ── L1 触发 ──
    if consecutive_timeouts >= l1_trigger_timeouts:
        return (DegradeLevel.L1, f"连续 {consecutive_timeouts} 次 dispatch 超时，降级到 L1")

    # ── L0 保持 ──
    if health_exit_code == 0:
        return (DegradeLevel.L0, "正常")

    # health exit=1 但不满足 L1 条件 → 保持当前
    return (current_level, f"health degraded 但未达阈值 (timeouts={consecutive_timeouts})")


def get_probe_interval(degrade_level: DegradeLevel, consecutive_fails: int) -> int:
    """根据降级级别返回探针间隔（秒）。

    L0: 固定 5s
    L1: 5s → 10s → 15s (按连续超时次数递增)
    L2: 15s → 30s → 60s → 120s (按连续失败次数递增)
    """
    if degrade_level == DegradeLevel.L0:
        return 5

    if degrade_level == DegradeLevel.L1:
        intervals = [5, 10, 15]
        idx = min(consecutive_fails, len(intervals) - 1)
        return intervals[idx]

    if degrade_level == DegradeLevel.L2:
        intervals = [15, 30, 60, 120]
        idx = min(consecutive_fails, len(intervals) - 1)
        return intervals[idx]

    return 30  # fallback


def make_degrade_event(
    from_level: DegradeLevel,
    to_level: DegradeLevel,
    detail: str,
    *,
    pending_count: int = 0,
    dead_count: int = 0,
) -> dict:
    """生成 Observer 降级状态变更事件。

    格式对齐 AIM Client v1.2 Observer 事件规范。
    """
    return {
        "event": "state_change",
        "type": "degrade",
        "from": from_level.value,
        "to": to_level.value,
        "detail": detail,
        "pending_count": pending_count,
        "dead_count": dead_count,
        "ts": time.time(),
    }
