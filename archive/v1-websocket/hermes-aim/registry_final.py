"""
AIM Registry — 最终精简版 (NATS 架构)
目标：≤200 行
"""
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).parent

@dataclass
class Operator:
    operator_id: str
    name: str
    role: str = "developer"
    status: str = "active"
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

@dataclass
class RegisteredAgent:
    agent_id: str
    operator_id: str
    agent_name: str
    emoji: str = "🤖"
    framework: str = ""
    version: str = ""
    secret_hash: str = ""
    status: str = "active"
    capabilities: dict = field(default_factory=dict)
    registered_at: float = 0.0
    last_seen: float = 0.0
    online: bool = False
    @property
    def is_active(self) -> bool:
        return self.status == "active"

@dataclass
class RegisterRequest:
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
    success: bool
    agent_id: str = ""
    agent_secret: str = ""
    reason: str = ""
    failed_check: str = ""
    operator_id: str = ""
    agents_remaining: int = 0

class OperatorRegistry:
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

class AgentRegistry:
    def __init__(self, operator_registry: OperatorRegistry):
        self._agents: Dict[str, RegisteredAgent] = {}
        self._next_id = 4
        self._operators = operator_registry
    def set_next_id(self, start: int):
        self._next_id = max(start, 3)
    def register(self, request: RegisterRequest) -> RegisterResult:
        operator = self._operators.get(request.operator_id)
        if not operator or not operator.is_active:
            return RegisterResult(False, reason="操作人不存在或状态异常", failed_check="operator_valid")
        if len(operator.agent_ids) >= operator.max_agents:
            return RegisterResult(False, reason=f"已达注册上限（{len(operator.agent_ids)}/{operator.max_agents}）", failed_check="agent_limit")
        if not request.agent_name or len(request.agent_name) > 32:
            return RegisterResult(False, reason="Agent 名称不合法（1-32 字符）", failed_check="info_valid")
        if not request.framework or len(request.framework) > 64:
            return RegisterResult(False, reason="framework 不合法（1-64 字符）", failed_check="info_valid")
        for aid in self._operators.get_agents_for(request.operator_id):
            existing = self._agents.get(aid)
            if existing and existing.is_active and existing.online:
                if existing.agent_name == request.agent_name and existing.framework == request.framework:
                    return RegisterResult(False, reason=f"同名 Agent 已在线: {aid}", failed_check="no_duplicate")
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
        self._save_secret(agent_id, agent_secret)
        self._operators.link_agent(request.operator_id, agent_id)
        return RegisterResult(
            success=True,
            agent_id=agent_id,
            agent_secret=agent_secret,
            operator_id=request.operator_id,
            agents_remaining=operator.agents_remaining,
        )
    def authenticate(self, agent_id: str, signature: str, timestamp: int) -> Tuple[bool, str]:
        agent = self._agents.get(agent_id)
        if not agent or agent.status != "active":
            return False, "Agent 不存在或状态异常"
        expected = hmac.new(
            agent.secret_hash.encode(),
            f"{agent_id}:{timestamp}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            return True, ""
        from security import get_security_manager
        sec = get_security_manager()
        if sec.verify_signature(agent_id, timestamp, signature):
            return True, ""
        return False, "签名验证失败"
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
    def _save_secret(self, agent_id: str, secret: str):
        try:
            secrets_dir = BASE_DIR / "secrets"
            secrets_dir.mkdir(exist_ok=True)
            secret_file = secrets_dir / f"{agent_id}.secret"
            secret_file.write_text(secret)
            secret_file.chmod(0o600)
        except Exception:
            pass
