"""
AIM Registry — 精简版 (NATS 架构)
只保留核心注册逻辑，删除 WebSocket/连接池/限流/状态管理

原版 843 行 → 精简版 ~200 行
"""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).parent


# ── 数据模型 ─────────────────────────

@dataclass
class Operator:
    """操作人 — 现实 Agent 操控者"""
    operator_id: str
    name: str
    role: str = "developer"          # admin | developer | viewer
    status: str = "active"           # active | suspended | frozen
    max_agents: int = 5
    agent_ids: List[str] = field(default_factory=list)
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
            "max_agents": self.max_agents,
            "agents_remaining": self.agents_remaining,
            "agent_count": len(self.agent_ids),
        }


@dataclass
class RegisteredAgent:
    """已注册的 Agent"""
    agent_id: str
    operator_id: str
    agent_name: str
    emoji: str = "🤖"
    framework: str = ""
    version: str = ""
    secret_hash: str = ""
    status: str = "active"            # pending | active | suspended | removed
    capabilities: dict = field(default_factory=dict)
    registered_at: float = 0.0
    last_seen: float = 0.0
    online: bool = False

    @property
    def is_active(self) -> bool:
        return self.status == "active"

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


@dataclass
class RegisterRequest:
    """Agent 发起的注册请求"""
    agent_name: str
    emoji: str = "🤖"
    framework: str = ""
    version: str = ""
    operator_id: str = ""
    capabilities: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "RegisterRequest":
        return cls(
            agent_name=data.get("agent_name", ""),
            emoji=data.get("emoji", "🤖"),
            framework=data.get("framework", ""),
            version=data.get("version", ""),
            operator_id=data.get("operator_id", ""),
            capabilities=data.get("capabilities", {}),
        )


@dataclass
class RegisterResult:
    """注册结果"""
    success: bool
    agent_id: str = ""
    agent_secret: str = ""
    reason: str = ""
    failed_check: str = ""
    operator_id: str = ""
    agents_remaining: int = 0


# ── 注册表 ─────────────────────────

class OperatorRegistry:
    """操作人注册表 — 由 Server 管理员手动维护"""

    def __init__(self):
        self._operators: Dict[str, Operator] = {}

    def add(self, operator: Operator):
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

    def load_from_config(self, config_operators: dict):
        """从 config.json 加载操作人"""
        for op_id, cfg in config_operators.items():
            op = Operator(
                operator_id=op_id,
                name=cfg.get("name", op_id),
                role=cfg.get("role", "developer"),
                status=cfg.get("status", "active"),
                max_agents=cfg.get("max_agents", 5),
            )
            self._operators[op_id] = op


class AgentRegistry:
    """Agent 注册表 — 自动化准入制（NATS 精简版）"""

    def __init__(self, operator_registry: OperatorRegistry):
        self._agents: Dict[str, RegisteredAgent] = {}
        self._next_id = 4
        self._operators = operator_registry

    def set_next_id(self, start: int):
        self._next_id = max(start, 3)

    # ── 核心：注册流程 ──

    def register(self, request: RegisterRequest) -> RegisterResult:
        """注册新 Agent — 简化版，只检查核心条件"""
        # 检查操作人
        operator = self._operators.get(request.operator_id)
        if not operator:
            return RegisterResult(False, reason="操作人不存在", failed_check="operator_valid")
        if not operator.is_active:
            return RegisterResult(False, reason=f"操作人状态异常: {operator.status}", failed_check="operator_valid")

        # 检查注册数量
        if len(operator.agent_ids) >= operator.max_agents:
            return RegisterResult(False, reason=f"已达 Agent 注册上限（{len(operator.agent_ids)}/{operator.max_agents}）", failed_check="agent_limit")

        # 检查基本信息
        if not request.agent_name or len(request.agent_name) > 32:
            return RegisterResult(False, reason="Agent 名称不合法（1-32 字符）", failed_check="info_valid")
        if not request.framework or len(request.framework) > 64:
            return RegisterResult(False, reason="framework 不合法（1-64 字符）", failed_check="info_valid")

        # 检查重复
        operator_agents = self._operators.get_agents_for(request.operator_id)
        for aid in operator_agents:
            existing = self._agents.get(aid)
            if existing and existing.is_active and existing.online:
                if existing.agent_name == request.agent_name and existing.framework == request.framework:
                    return RegisterResult(False, reason=f"同名 Agent 已在线: {aid} ({request.agent_name}/{request.framework})", failed_check="no_duplicate")

        # 全部通过，创建 Agent
        agent_id = f"ZS{self._next_id:04d}"
        self._next_id += 1

        agent_secret = f"sk-aim-{secrets.token_hex(24)}"
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
            capabilities=request.capabilities,
            registered_at=time.time(),
            last_seen=time.time(),
            online=False,
        )
        self._agents[agent_id] = agent

        # 写入 secrets 文件
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

    # ── 认证 ──

    def authenticate(self, agent_id: str, signature: str, timestamp: int) -> Tuple[bool, str]:
        """HMAC 签名认证"""
        agent = self._agents.get(agent_id)
        if not agent:
            return False, "Agent 不存在"
        if agent.status != "active":
            return False, f"Agent 状态异常: {agent.status}"

        # 用 agent_secret_hash 验证
        expected = hmac.new(
            agent.secret_hash.encode(),
            f"{agent_id}:{timestamp}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            return True, ""

        # 用 security.py 验证（种子 Agent）
        from security import get_security_manager
        sec = get_security_manager()
        if sec.verify_signature(agent_id, timestamp, signature):
            return True, ""

        return False, "签名验证失败"

    # ── Agent 状态管理（简化版） ──

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

    # ── 种子 Agent（向后兼容） ──

    def _save_secret(self, agent_id: str, secret: str):
        """将 agent_secret 写入 secrets 文件"""
        try:
            secrets_dir = BASE_DIR / "secrets"
            secrets_dir.mkdir(exist_ok=True)
            secret_file = secrets_dir / f"{agent_id}.secret"
            secret_file.write_text(secret)
            secret_file.chmod(0o600)
        except Exception:
            pass

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
