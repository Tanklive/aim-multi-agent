"""
AIM Registry — 精简版 (NATS 架构)
只保留核心注册逻辑，删除 WebSocket/连接池/限流/状态管理

原版 843 行 → 精简版 ~150 行
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
    """操作人"""
    operator_id: str
    name: str
    role: str = "developer"  # admin | developer | viewer
    status: str = "active"   # active | suspended
    max_agents: int = 5
    agent_ids: List[str] = field(default_factory=list)

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
    status: str = "active"  # active | removed
    registered_at: float = 0.0
    last_seen: float = 0.0

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "emoji": self.emoji,
            "framework": self.framework,
            "status": self.status,
            "operator_id": self.operator_id,
        }


@dataclass
class RegisterRequest:
    """注册请求"""
    agent_name: str
    operator_id: str
    emoji: str = "🤖"
    framework: str = ""
    version: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "RegisterRequest":
        return cls(
            agent_name=data.get("agent_name", ""),
            operator_id=data.get("operator_id", ""),
            emoji=data.get("emoji", "🤖"),
            framework=data.get("framework", ""),
            version=data.get("version", ""),
        )


@dataclass
class RegisterResult:
    """注册结果"""
    success: bool
    agent_id: str = ""
    agent_secret: str = ""
    reason: str = ""


# ── 注册表 ─────────────────────────

class OperatorRegistry:
    """操作人注册表"""

    def __init__(self):
        self._operators: Dict[str, Operator] = {}

    def add(self, operator: Operator):
        self._operators[operator.operator_id] = operator

    def get(self, operator_id: str) -> Optional[Operator]:
        return self._operators.get(operator_id)

    def list_active(self) -> List[Operator]:
        return [op for op in self._operators.values() if op.is_active]

    def link_agent(self, operator_id: str, agent_id: str) -> bool:
        op = self._operators.get(operator_id)
        if not op:
            return False
        if agent_id not in op.agent_ids:
            op.agent_ids.append(agent_id)
        return True

    def unlink_agent(self, operator_id: str, agent_id: str) -> bool:
        op = self._operators.get(operator_id)
        if not op:
            return False
        op.agent_ids = [aid for aid in op.agent_ids if aid != agent_id]
        return True

    def load_from_config(self, config_operators: dict):
        """从 config.json 加载操作人"""
        for op_id, cfg in config_operators.items():
            self._operators[op_id] = Operator(
                operator_id=op_id,
                name=cfg.get("name", op_id),
                role=cfg.get("role", "developer"),
                status=cfg.get("status", "active"),
                max_agents=cfg.get("max_agents", 5),
            )


class AgentRegistry:
    """Agent 注册表 — 自动化准入"""

    def __init__(self, operator_registry: OperatorRegistry):
        self._agents: Dict[str, RegisteredAgent] = {}
        self._next_id = 4
        self._operators = operator_registry

    def set_next_id(self, start: int):
        self._next_id = max(start, 3)

    def register(self, request: RegisterRequest) -> RegisterResult:
        """注册新 Agent"""
        # 检查操作人
        operator = self._operators.get(request.operator_id)
        if not operator or not operator.is_active:
            return RegisterResult(False, reason="操作人不存在或已暂停")

        # 检查上限
        if len(operator.agent_ids) >= operator.max_agents:
            return RegisterResult(False, reason=f"已达上限 ({operator.max_agents})")

        # 检查名称
        if not request.agent_name or len(request.agent_name) > 32:
            return RegisterResult(False, reason="名称不合法")

        # 创建 Agent
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
            registered_at=time.time(),
            last_seen=time.time(),
        )
        self._agents[agent_id] = agent
        self._operators.link_agent(request.operator_id, agent_id)

        # 写入 secret 文件
        self._save_secret(agent_id, agent_secret)

        return RegisterResult(True, agent_id=agent_id, agent_secret=agent_secret)

    def authenticate(self, agent_id: str, signature: str, timestamp: int) -> Tuple[bool, str]:
        """HMAC 签名认证"""
        agent = self._agents.get(agent_id)
        if not agent:
            return False, "Agent 不存在"
        if agent.status != "active":
            return False, f"Agent 状态异常: {agent.status}"

        expected = hmac.new(
            agent.secret_hash.encode(),
            f"{agent_id}:{timestamp}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            return True, ""

        # 兼容种子 Agent
        try:
            from security import get_security_manager
            sec = get_security_manager()
            if sec.verify_signature(agent_id, timestamp, signature):
                return True, ""
        except ImportError:
            pass

        return False, "签名验证失败"

    def get(self, agent_id: str) -> Optional[RegisteredAgent]:
        return self._agents.get(agent_id)

    def remove(self, agent_id: str) -> bool:
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        agent.status = "removed"
        self._operators.unlink_agent(agent.operator_id, agent_id)
        return True

    def list_active(self) -> List[RegisteredAgent]:
        return [a for a in self._agents.values() if a.is_active]

    def add_seed(self, agent_id: str, operator_id: str, agent_name: str,
                 emoji: str = "🤖", framework: str = ""):
        """添加种子 Agent"""
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

    @staticmethod
    def _save_secret(agent_id: str, secret: str):
        """写入 secret 文件"""
        try:
            secrets_dir = BASE_DIR / "secrets"
            secrets_dir.mkdir(exist_ok=True)
            secret_file = secrets_dir / f"{agent_id}.secret"
            secret_file.write_text(secret)
            secret_file.chmod(0o600)
        except Exception:
            pass
