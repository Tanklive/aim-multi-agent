"""
AIM Registry — OperatorRegistry + AgentRegistry
注册制核心：操作人身份绑定 + 自动化准入 5 标准

设计文档：~/shared/aim/AIM-AGENT-REGISTRATION.md
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).parent

# ── 常量 ─────────────────────────────

CHANNEL_WHITELIST = {"main", "script", "health", "web", "mobile", "qq"}
EXT_CHANNEL_RE = re.compile(r"^ext:[a-z0-9_]{1,16}$")

DEFAULT_MAX_AGENTS_DEVELOPER = 5
DEFAULT_MAX_AGENTS_ADMIN = 20

REGISTER_RATE_LIMIT_IP_HOUR = 3      # 每 IP 每小时最多 N 次
REGISTER_RATE_LIMIT_GLOBAL_DAY = 50  # 全局每日最多 N 次
REGISTER_RATE_LIMIT_WINDOW_HOUR = 3600
REGISTER_RATE_LIMIT_WINDOW_DAY = 86400

OFFLINE_EXPIRE_SECONDS = 86400  # 24h 离线可被顶替
HEARTBEAT_TIMEOUT = 900         # 15min 无心跳标记 offline
REGISTRY_EXPIRE_SECONDS = 86400 # 24h 无有效连接 → 清理注册


# ── 数据模型 ─────────────────────────

@dataclass
class Operator:
    """操作人 — 现实 Agent 操控者"""
    operator_id: str
    name: str
    role: str = "developer"          # admin | developer | viewer
    status: str = "active"           # active | suspended | frozen

    # 联系方式（当前 manual 模式，纯备注）
    phone: str = ""
    email: str = ""

    # 认证方式（预留）
    verify_method: str = "manual"    # manual | phone | email | oauth
    identity_verified: bool = False

    # 注册上限
    max_agents: int = DEFAULT_MAX_AGENTS_DEVELOPER

    # 名下 Agent
    agent_ids: List[str] = field(default_factory=list)

    # 时间戳
    registered_at: float = 0.0
    last_seen: float = 0.0

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def agents_remaining(self) -> int:
        return max(0, self.max_agents - len(self.agent_ids))

    def to_dict(self) -> dict:
        return {
            "operator_id": self.operator_id,
            "name": self.name,
            "role": self.role,
            "status": self.status,
            "verify_method": self.verify_method,
            "max_agents": self.max_agents,
            "agents_remaining": self.agents_remaining,
            "agent_count": len(self.agent_ids),
        }


@dataclass
class RegisteredAgent:
    """已注册的 Agent"""
    agent_id: str
    operator_id: str                   # 绑定的操作人

    # Agent 自声明
    agent_name: str
    emoji: str = "🤖"
    framework: str = ""
    version: str = ""

    # 注册状态
    secret_hash: str = ""              # agent_secret 的 SHA256（Server 不存明文）
    status: str = "active"            # pending | active | suspended | removed

    # 框架信息（由 P3 CLI 抽象层消费）
    cli_path: str = ""
    commands: dict = field(default_factory=dict)   # {"chat": "...", "health": "..."}
    timeout: int = 120

    # 能力声明
    capabilities: dict = field(default_factory=dict)  # {"提供": [...], "需求": [...]}

    # 连接信息
    channels: list = field(default_factory=lambda: ["main"])
    handler: bool = True

    # 时间戳
    registered_at: float = 0.0
    last_seen: float = 0.0
    online: bool = False

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_offline_expired(self) -> bool:
        """是否离线超过过期时间"""
        if self.online:
            return False
        return (time.time() - self.last_seen) > OFFLINE_EXPIRE_SECONDS

    def to_public_dict(self) -> dict:
        """对外暴露的信息（不含 secret_hash）"""
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "emoji": self.emoji,
            "framework": self.framework,
            "version": self.version,
            "online": self.online,
            "capabilities": self.capabilities,
            "operator_id": self.operator_id,
        }


# ── 注册请求/响应模型 ────────────────

@dataclass
class RegisterRequest:
    """Agent 发起的注册请求"""
    agent_name: str
    emoji: str = "🤖"
    framework: str = ""
    version: str = ""
    operator_id: str = ""  # 由 Server 分配，Agent 注册时携带

    capabilities: dict = field(default_factory=dict)
    cli_path: str = ""
    commands: dict = field(default_factory=dict)
    timeout: int = 120
    channels: list = field(default_factory=lambda: ["main"])
    handler: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "RegisterRequest":
        return cls(
            agent_name=data.get("agent_name", ""),
            emoji=data.get("emoji", "🤖"),
            framework=data.get("framework", ""),
            version=data.get("version", ""),
            operator_id=data.get("operator_id", ""),
            capabilities=data.get("capabilities", {}),
            cli_path=data.get("cli_path", ""),
            commands=data.get("commands", {}),
            timeout=data.get("timeout", 120),
            channels=data.get("channels", ["main"]),
            handler=data.get("handler", True),
        )


@dataclass
class RegisterResult:
    """注册结果"""
    success: bool
    agent_id: str = ""
    agent_secret: str = ""
    reason: str = ""
    failed_check: str = ""    # 哪个标准失败了
    operator_id: str = ""
    agents_remaining: int = 0


# ── 限流器 ─────────────────────────

class RateLimiter:
    """简易滑动窗口限流器"""

    def __init__(self):
        self._ip_records: Dict[str, list] = {}      # ip -> [timestamps]
        self._global_records: list = []              # 全局注册时间戳

    def check_ip(self, ip: str) -> Tuple[bool, str]:
        """检查 IP 频率"""
        now = time.time()
        window = REGISTER_RATE_LIMIT_WINDOW_HOUR
        max_count = REGISTER_RATE_LIMIT_IP_HOUR

        records = self._ip_records.get(ip, [])
        records = [t for t in records if now - t < window]

        if len(records) >= max_count:
            return False, f"注册频率过高（IP 每小时上限 {max_count} 次）"

        records.append(now)
        self._ip_records[ip] = records
        return True, ""

    def check_global(self) -> Tuple[bool, str]:
        """检查全局日限流"""
        now = time.time()
        window = REGISTER_RATE_LIMIT_WINDOW_DAY
        max_count = REGISTER_RATE_LIMIT_GLOBAL_DAY

        self._global_records = [t for t in self._global_records if now - t < window]

        if len(self._global_records) >= max_count:
            return False, f"全局注册已达日上限（{max_count} 次）"

        self._global_records.append(now)
        return True, ""


# ── 注册表 ─────────────────────────

class OperatorRegistry:
    """操作人注册表 — 由 Server 管理员手动维护"""

    def __init__(self):
        self._operators: Dict[str, Operator] = {}

    def add(self, operator: Operator):
        """添加操作人"""
        self._operators[operator.operator_id] = operator

    def get(self, operator_id: str) -> Optional[Operator]:
        return self._operators.get(operator_id)

    def remove(self, operator_id: str) -> bool:
        if operator_id in self._operators:
            del self._operators[operator_id]
            return True
        return False

    def list_all(self) -> List[Operator]:
        return list(self._operators.values())

    def list_active(self) -> List[Operator]:
        return [op for op in self._operators.values() if op.is_active]

    # ── 操作人状态管理 ──

    def suspend(self, operator_id: str) -> bool:
        """暂停操作人（名下所有 Agent 离线）"""
        op = self._operators.get(operator_id)
        if not op:
            return False
        op.status = "suspended"
        return True

    def activate(self, operator_id: str) -> bool:
        op = self._operators.get(operator_id)
        if not op:
            return False
        op.status = "active"
        return True

    def freeze(self, operator_id: str) -> bool:
        """冻结操作人（额外标记，行为同 suspend）"""
        return self.suspend(operator_id)

    # ── Agent 绑定 ──

    def link_agent(self, operator_id: str, agent_id: str) -> bool:
        op = self._operators.get(operator_id)
        if not op:
            return False
        if agent_id not in op.agent_ids:
            op.agent_ids.append(agent_id)
        op.last_seen = time.time()
        return True

    def unlink_agent(self, operator_id: str, agent_id: str) -> bool:
        op = self._operators.get(operator_id)
        if not op:
            return False
        op.agent_ids = [aid for aid in op.agent_ids if aid != agent_id]
        return True

    def get_agents_for(self, operator_id: str) -> List[str]:
        op = self._operators.get(operator_id)
        return list(op.agent_ids) if op else []

    # ── 配置持久化 ──

    def to_config_dict(self) -> dict:
        """导出为 config.json 兼容格式"""
        result = {}
        for op in self._operators.values():
            result[op.operator_id] = {
                "name": op.name,
                "role": op.role,
                "phone": op.phone,
                "email": op.email,
                "verify_method": op.verify_method,
                "identity_verified": op.identity_verified,
                "max_agents": op.max_agents,
                "status": op.status,
            }
        return result

    def load_from_config(self, config_operators: dict):
        """从 config.json 加载操作人"""
        for op_id, cfg in config_operators.items():
            op = Operator(
                operator_id=op_id,
                name=cfg.get("name", op_id),
                role=cfg.get("role", "developer"),
                status=cfg.get("status", "active"),
                phone=cfg.get("phone", ""),
                email=cfg.get("email", ""),
                verify_method=cfg.get("verify_method", "manual"),
                identity_verified=cfg.get("identity_verified", False),
                max_agents=cfg.get("max_agents", DEFAULT_MAX_AGENTS_DEVELOPER),
            )
            self._operators[op_id] = op


class AgentRegistry:
    """Agent 注册表 — 自动化准入制"""

    def __init__(self, operator_registry: OperatorRegistry):
        self._agents: Dict[str, RegisteredAgent] = {}
        self._next_id = 4  # 默认，会在 _init_registry 中被 config 覆盖
        self._lock = None
        self._operators = operator_registry
        self._rate_limiter = RateLimiter()

    def set_next_id(self, start: int):
        """设置 ID 起始值"""
        self._next_id = max(start, 3)  # 不低于 ZS0003

    # ── 核心：注册流程（自动化准入 5 标准） ──

    def register(self, request: RegisterRequest, client_ip: str = "") -> RegisterResult:
        """
        注册新 Agent — 依次检查 5 条自动化准入标准
        任一不通过 → register_denied，全部通过 → register_ok
        """
        # 标准 1：操作人合法
        operator = self._operators.get(request.operator_id)
        if not operator:
            return RegisterResult(False, reason="操作人不存在",
                                  failed_check="operator_valid")
        if not operator.is_active:
            return RegisterResult(False, reason=f"操作人状态异常: {operator.status}",
                                  failed_check="operator_valid")

        # 标准 2：注册数量未达上限
        if len(operator.agent_ids) >= operator.max_agents:
            return RegisterResult(False, reason=f"已达 Agent 注册上限（{len(operator.agent_ids)}/{operator.max_agents}）",
                                  failed_check="agent_limit")

        # 标准 3：基本信息声明完整合法
        info_ok, info_msg = self._check_info_valid(request)
        if not info_ok:
            return RegisterResult(False, reason=info_msg,
                                  failed_check="info_valid")

        # 标准 4：无重复注册
        dup_ok, dup_msg = self._check_no_duplicate(request)
        if not dup_ok:
            return RegisterResult(False, reason=dup_msg,
                                  failed_check="no_duplicate")

        # 标准 5：限流
        if client_ip:
            ip_ok, ip_msg = self._rate_limiter.check_ip(client_ip)
            if not ip_ok:
                return RegisterResult(False, reason=ip_msg,
                                      failed_check="rate_limit")
        global_ok, global_msg = self._rate_limiter.check_global()
        if not global_ok:
            return RegisterResult(False, reason=global_msg,
                                  failed_check="rate_limit")

        # ── 全部通过，创建 Agent ──
        agent_id = f"ZS{self._next_id:04d}"
        self._next_id += 1

        agent_secret = self._generate_secret()
        secret_hash = hashlib.sha256(agent_secret.encode()).hexdigest()

        agent = RegisteredAgent(
            agent_id=agent_id,
            operator_id=request.operator_id,
            agent_name=request.agent_name,
            emoji=request.emoji,
            framework=request.framework,
            version=request.version,
            secret_hash=secret_hash,
            status="active",
            cli_path=request.cli_path,
            commands=request.commands,
            timeout=request.timeout,
            capabilities=request.capabilities,
            channels=request.channels,
            handler=request.handler,
            registered_at=time.time(),
            last_seen=time.time(),
            online=False,
        )
        self._agents[agent_id] = agent

        # 写入 secrets 文件（供 security.py 认证用）
        self._save_secret(agent_id, agent_secret)

        # 绑定到操作人
        self._operators.link_agent(request.operator_id, agent_id)

        return RegisterResult(
            success=True,
            agent_id=agent_id,
            agent_secret=agent_secret,
            operator_id=request.operator_id,
            agents_remaining=operator.agents_remaining,
        )

    # ── 准入标准检查 ──

    @staticmethod
    def _check_info_valid(req: RegisterRequest) -> Tuple[bool, str]:
        """标准 3：基本信息合法性"""
        if not req.agent_name or len(req.agent_name) > 32:
            return False, "Agent 名称不合法（1-32 字符）"
        if not req.framework or len(req.framework) > 64:
            return False, "framework 不合法（1-64 字符）"
        if req.commands and not isinstance(req.commands, dict):
            return False, "commands 必须是字典格式"
        if req.commands and "chat" not in req.commands:
            return False, "commands 必须包含 chat 命令模板"
        # channel 白名单校验
        for ch in req.channels:
            if ch not in CHANNEL_WHITELIST and not EXT_CHANNEL_RE.match(ch):
                return False, f"不支持的 channel: {ch}"
        return True, ""

    def _check_no_duplicate(self, req: RegisterRequest) -> Tuple[bool, str]:
        """标准 4：无重复注册"""
        operator_agents = self._operators.get_agents_for(req.operator_id)
        for aid in operator_agents:
            existing = self._agents.get(aid)
            if existing and existing.is_active and existing.online:
                if existing.agent_name == req.agent_name and existing.framework == req.framework:
                    return False, f"同名 Agent 已在线: {aid} ({req.agent_name}/{req.framework})"
        # 离线 24h 可被顶替 — 不做拒绝，直接允许覆盖
        return True, ""

    # ── 认证 ──

    def authenticate(self, agent_id: str, signature: str, timestamp: int) -> Tuple[bool, str]:
        """HMAC 签名认证（兼容注册制新 Agent 和种子 Agent）"""
        agent = self._agents.get(agent_id)
        if not agent:
            return False, "Agent 不存在"
        if agent.status != "active":
            return False, f"Agent 状态异常: {agent.status}"

        # 方式1：用 agent_secret_hash 验证（注册制新 Agent）
        expected = hmac.new(
            agent.secret_hash.encode(),
            f"{agent_id}:{timestamp}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            return True, ""

        # 方式2：用 security.py 验证（种子 Agent，从 secrets/ 文件加载）
        from security import get_security_manager
        sec = get_security_manager()
        if sec.verify_signature(agent_id, timestamp, signature):
            return True, ""

        return False, "签名验证失败"

    # ── Agent 状态管理 ──

    def get(self, agent_id: str) -> Optional[RegisteredAgent]:
        return self._agents.get(agent_id)

    def mark_online(self, agent_id: str):
        agent = self._agents.get(agent_id)
        if agent:
            agent.online = True
            agent.last_seen = time.time()

    def mark_offline(self, agent_id: str):
        agent = self._agents.get(agent_id)
        if agent:
            agent.online = False
            agent.last_seen = time.time()

    def remove(self, agent_id: str) -> bool:
        """注销 Agent — ID 不回收"""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        agent.status = "removed"
        agent.online = False
        self._operators.unlink_agent(agent.operator_id, agent_id)
        return True

    def list_active(self) -> List[RegisteredAgent]:
        return [a for a in self._agents.values() if a.is_active]

    def list_online(self) -> List[RegisteredAgent]:
        return [a for a in self._agents.values() if a.is_active and a.online]

    def detect_conflict_on_reconnect(self, agent_id: str, agent_name: str, framework: str) -> Optional[str]:
        """
        标准 4 补充：旧 Agent 重连时检测同名冲突
        如果存在同操作人 + 同名 + 同 framework 的在线 Agent（且不是自己）→ 返回冲突的 agent_id
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return None
        op_agents = self._operators.get_agents_for(agent.operator_id)
        for aid in op_agents:
            if aid == agent_id:
                continue
            existing = self._agents.get(aid)
            if existing and existing.is_active and existing.online:
                if existing.agent_name == agent_name and existing.framework == framework:
                    return aid
        return None

    # ── 种子 Agent（向后兼容） ──

    @staticmethod
    def _save_secret(agent_id: str, secret: str):
        """将 agent_secret 写入 secrets 文件（供 security.py 认证）"""
        try:
            secrets_dir = BASE_DIR / "secrets"
            secrets_dir.mkdir(exist_ok=True)
            secret_file = secrets_dir / f"{agent_id}.secret"
            secret_file.write_text(secret)
            secret_file.chmod(0o600)  # 仅 owner 可读写
        except Exception:
            pass  # 写入失败不影响注册

    def add_seed(self, agent_id: str, operator_id: str, agent_name: str,
                 emoji: str = "🤖", framework: str = ""):
        """添加种子 Agent（不走注册流程，从 config.json 预加载）"""
        if agent_id in self._agents:
            return
        agent = RegisteredAgent(
            agent_id=agent_id,
            operator_id=operator_id,
            agent_name=agent_name,
            emoji=emoji,
            framework=framework,
            status="active",
            registered_at=time.time(),
            last_seen=time.time(),
        )
        self._agents[agent_id] = agent
        self._operators.link_agent(operator_id, agent_id)

    # ── 工具 ──

    @staticmethod
    def _generate_secret() -> str:
        return f"sk-aim-{secrets.token_hex(24)}"


# ── 状态管理器 ─────────────────────────

@dataclass
class AgentState:
    """Agent 实时状态（由 Server 维护，不在 RegisteredAgent 中持久化）"""
    agent_id: str
    status: str = "offline"            # online / busy / offline / error
    last_heartbeat: float = 0.0
    connected_at: float = time.time()
    load: dict = field(default_factory=lambda: {"cpu": 0.0, "memory": 0.0, "pending_tasks": 0})
    old_status: str = "offline"        # 用于状态变更检测

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "last_heartbeat": self.last_heartbeat,
            "connected_at": self.connected_at,
            "uptime": time.time() - self.connected_at if self.connected_at else 0,
            "load": self.load,
        }


class AgentStateManager:
    """
    Agent 状态管理器 — 统一管理心跳 + 状态变更 + 超时检测

    职责：
    1. 接收心跳 → 更新时间戳 + 负载
    2. 状态变更检测 → 生成事件
    3. 超时扫描 → 标记 offline + 生成事件

    通知回调由外部注入（node.py 的广播函数）
    """

    # 心跳超时阈值（秒）
    HEARTBEAT_TIMEOUT = 90      # 3 次未收到心跳
    SCAN_INTERVAL = 15          # 超时扫描间隔
    OFFLINE_COOLDOWN = 5        # 标记 offline 后冷却时间（秒）

    def __init__(self):
        self._states: Dict[str, AgentState] = {}
        # 回调：当生命周期事件发生时调用
        # 签名: callback(event_type: str, data: dict)
        self._lifecycle_callback = None
        # 超时回调：当扫描到 Agent 心跳超时后调用
        # 签名: callback(agent_id: str)
        self._timeout_callback = None
        self._scan_task: Optional[asyncio.Task] = None
        self._running = False
        # 冷却记录：agent_id -> offline_at 时间戳
        self._offline_cooldowns: Dict[str, float] = {}

    def set_lifecycle_callback(self, callback):
        """设置生命周期事件回调（由 node.py 注入广播函数）"""
        self._lifecycle_callback = callback

    def set_timeout_callback(self, callback):
        """设置超时回调，心跳超时时通知外部清理连接池"""
        self._timeout_callback = callback

    # ── 状态查询 ──

    def get(self, agent_id: str) -> Optional[AgentState]:
        return self._states.get(agent_id)

    def ensure(self, agent_id: str) -> AgentState:
        """获取或创建 AgentState"""
        if agent_id not in self._states:
            self._states[agent_id] = AgentState(agent_id=agent_id)
        return self._states[agent_id]

    def is_online(self, agent_id: str) -> bool:
        state = self._states.get(agent_id)
        return state is not None and state.status in ("online", "busy")

    def online_list(self) -> list:
        """获取所有在线 Agent 列表"""
        return [
            state.to_dict()
            for state in self._states.values()
            if state.status in ("online", "busy")
        ]

    # ── 心跳处理 ──

    def _is_in_cooldown(self, agent_id: str) -> bool:
        """检查 Agent 是否在冷却期内（标记 offline 后 5 秒内不接受 heartbeat）"""
        offline_at = self._offline_cooldowns.get(agent_id)
        if offline_at is None:
            return False
        if time.time() - offline_at < self.OFFLINE_COOLDOWN:
            return True
        # 冷却期已过，清理
        del self._offline_cooldowns[agent_id]
        return False

    def _set_cooldown(self, agent_id: str):
        """标记 Agent 进入冷却期"""
        self._offline_cooldowns[agent_id] = time.time()

    def handle_heartbeat(self, agent_id: str,
                         status: str = "online",
                         load: Optional[dict] = None) -> Optional[str]:
        """
        处理心跳，返回旧状态（如果有状态变更则返回旧状态，否则 None）

        冷却期处理：如果 Agent 在冷却期内收到心跳，说明 Agent 仍然在线，
        不应拒绝心跳，而应立刻从冷却/offline 状态恢复。冷却期防的是
        断连残留误判，不是防合法心跳。
        """
        state = self.ensure(agent_id)
        now = time.time()

        # 冷却期内收到合法心跳 → Agent 仍然在线，立即恢复
        if self._is_in_cooldown(agent_id):
            self._offline_cooldowns.pop(agent_id, None)

        old_status = state.status
        state.status = status if status in ("online", "busy", "error") else "online"
        state.last_heartbeat = now
        state.old_status = old_status

        if load:
            state.load = {
                "pending_tasks": max(load.get("pending_tasks", 0), 0),
            }

        # 首次上线或从 offline 恢复
        if old_status == "offline" and state.status in ("online", "busy"):
            state.connected_at = now

        # 状态变更（包括 cooldown→online 的转换）
        if old_status != state.status:
            return old_status  # 旧状态，通知调用方广播 status_change + online/offline
        return None  # 无变更

    def handle_deregister(self, agent_id: str, reason: str = "graceful_shutdown"):
        """处理主动下线"""
        state = self._states.get(agent_id)
        if state:
            old_status = state.status
            state.status = "offline"
            state.old_status = old_status
            self._fire_lifecycle("agent_offline", agent_id, old_status, "offline", reason)

    def handle_connect(self, agent_id: str):
        """Agent 连入（auth 成功后调用）"""
        state = self.ensure(agent_id)
        old_status = state.status if state else "offline"
        state.status = "online"
        state.connected_at = time.time()
        state.last_heartbeat = time.time()
        state.old_status = old_status
        self._fire_lifecycle("agent_online", agent_id, old_status, "online", "auth_success")

    def handle_disconnect(self, agent_id: str):
        """Agent 断连"""
        state = self._states.get(agent_id)
        if state and state.status in ("online", "busy"):
            old_status = state.status
            state.status = "offline"
            state.old_status = old_status
            self._set_cooldown(agent_id)
            self._fire_lifecycle("agent_offline", agent_id, old_status, "offline", "connection_closed")

    # ── 超时检测 ──

    async def start_timeout_scanner(self):
        """启动后台超时扫描任务"""
        if self._running:
            return
        self._running = True
        self._scan_task = asyncio.create_task(self._timeout_scan_loop())

    async def stop_timeout_scanner(self):
        """停止超时扫描"""
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

    async def _timeout_scan_loop(self):
        """超时扫描循环：每 15 秒检查一次"""
        while self._running:
            try:
                await asyncio.sleep(self.SCAN_INTERVAL)
                self._scan_timeouts()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def _scan_timeouts(self):
        """扫描超时 Agent"""
        now = time.time()
        # 清理过期冷却记录
        expired_cooldowns = [aid for aid, t in self._offline_cooldowns.items()
                            if now - t > self.OFFLINE_COOLDOWN]
        for aid in expired_cooldowns:
            del self._offline_cooldowns[aid]

        for agent_id, state in list(self._states.items()):
            if state.status in ("online", "busy", "error"):
                elapsed = now - state.last_heartbeat
                if elapsed > self.HEARTBEAT_TIMEOUT:
                    old_status = state.status
                    state.status = "offline"
                    state.old_status = old_status
                    self._set_cooldown(agent_id)
                    self._fire_lifecycle(
                        "agent_offline", agent_id, old_status, "offline",
                        f"heartbeat_timeout ({int(elapsed)}s)"
                    )
                    # 通知外部清理连接池中该 agent 的过期连接
                    if self._timeout_callback:
                        try:
                            self._timeout_callback(agent_id)
                        except Exception:
                            pass

    # ── 生命周期状态查询 ──

    def lifecycle_status(self, agent_id: str = None) -> dict:
        """
        查询生命周期状态
        - lifecycle_status 或 lifecycle_status:all → 全部
        - lifecycle_status:ZS0001 → 单个
        """
        if agent_id and agent_id != "all":
            state = self._states.get(agent_id)
            if state:
                return {agent_id: state.to_dict()}
            return {agent_id: None}
        return {aid: s.to_dict() for aid, s in self._states.items()}

    # ── 生命周期广播 ──

    def _fire_lifecycle(self, event: str, agent_id: str,
                        old_status: str, new_status: str, reason: str = ""):
        """触发生命周期事件（通过回调广播）"""
        if not self._lifecycle_callback:
            return
        data = {
            "type": "agent_lifecycle",
            "event": event,
            "agent_id": agent_id,
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
            "timestamp": time.time(),
        }
        try:
            asyncio.ensure_future(self._lifecycle_callback(event, data))
        except Exception:
            pass
