"""
AIM Agent 本地配置文件格式定义 + 校验

每个 Agent 本地维护一个 config 文件，包含：
- 自声明信息（注册时提交给 Server）
- Server 连接信息
- 注册凭证（注册成功后写入）

文件格式：JSON
默认路径：~/.hermes/aim/agent_config.json（每个 Agent 独立）

设计文档：~/shared/aim/AIM-AGENT-REGISTRATION.md
"""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 常量 ─────────────────────────────

DEFAULT_CONFIG_DIR = Path.home() / ".hermes" / "aim"
DEFAULT_CONFIG_FILE = "agent_config.json"

# Channel 白名单（与 registry.py 保持一致）
CHANNEL_WHITELIST = {"main", "script", "health", "web", "mobile", "qq"}

# 支持的框架列表
SUPPORTED_FRAMEWORKS = {
    "openclaw", "hermes", "crewai", "autogen", "langchain",
    "custom", "other"
}

# ── 配置 Schema ─────────────────────

@dataclass
class AgentLocalConfig:
    """
    Agent 本地配置文件格式

    注册前（最小配置）：
    {
        "agent_name": "我的Agent",
        "emoji": "🤖",
        "framework": "openclaw",
        "version": "1.0.0",
        "operator_id": "OP0001",
        "hub": {"host": "127.0.0.1", "port": 18900},
        "commands": {"chat": "openclaw chat --agent {agent_id} --msg {message}"},
        "channels": ["main"]
    }

    注册成功后（Server 回填）：
    {
        ...上面的字段...
        "agent_id": "ZS0004",
        "agent_secret": "sk-aim-xxxxx",
        "registered_at": 1717590000.0
    }
    """

    # ── 自声明信息（注册时提交） ──
    agent_name: str = ""
    emoji: str = "🤖"
    framework: str = ""
    version: str = ""
    operator_id: str = ""

    # ── Hub 连接 ──
    hub_host: str = "127.0.0.1"
    hub_port: int = 18900

    # ── 能力声明 ──
    capabilities: dict = field(default_factory=dict)
    # 格式: {"提供": ["技能A", "技能B"], "需求": ["数据X"]}

    # ── CLI 抽象层 ──
    cli_path: str = ""
    commands: dict = field(default_factory=dict)
    # 格式: {"chat": "...", "health": "...", "status": "..."}
    timeout: int = 120

    # ── 通信 ──
    channels: list = field(default_factory=lambda: ["main"])
    handler: bool = True

    # ── Server 回填（注册成功后写入） ──
    agent_id: str = ""
    agent_secret: str = ""
    registered_at: float = 0.0

    # ── 元数据 ──
    config_version: str = "1.0"

    def to_dict(self) -> dict:
        """导出为 JSON 字典"""
        return {
            "agent_name": self.agent_name,
            "emoji": self.emoji,
            "framework": self.framework,
            "version": self.version,
            "operator_id": self.operator_id,
            "hub": {
                "host": self.hub_host,
                "port": self.hub_port,
            },
            "capabilities": self.capabilities,
            "cli_path": self.cli_path,
            "commands": self.commands,
            "timeout": self.timeout,
            "channels": self.channels,
            "handler": self.handler,
            # Server 回填
            "agent_id": self.agent_id,
            "agent_secret": self.agent_secret,
            "registered_at": self.registered_at,
            "config_version": self.config_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentLocalConfig":
        """从 JSON 字典加载"""
        hub = data.get("hub", {})
        return cls(
            agent_name=data.get("agent_name", ""),
            emoji=data.get("emoji", "🤖"),
            framework=data.get("framework", ""),
            version=data.get("version", ""),
            operator_id=data.get("operator_id", ""),
            hub_host=hub.get("host", "127.0.0.1"),
            hub_port=hub.get("port", 18900),
            capabilities=data.get("capabilities", {}),
            cli_path=data.get("cli_path", ""),
            commands=data.get("commands", {}),
            timeout=data.get("timeout", 120),
            channels=data.get("channels", ["main"]),
            handler=data.get("handler", True),
            agent_id=data.get("agent_id", ""),
            agent_secret=data.get("agent_secret", ""),
            registered_at=data.get("registered_at", 0.0),
            config_version=data.get("config_version", "1.0"),
        )

    @property
    def is_registered(self) -> bool:
        """是否已注册（有 agent_id 和 secret）"""
        return bool(self.agent_id and self.agent_secret)

    @property
    def server_url(self) -> str:
        """Hub WebSocket URL"""
        return f"ws://{self.hub_host}:{self.hub_port}"


# ── 校验 ─────────────────────────────

class ConfigValidationError(Exception):
    """配置校验失败"""
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"[{field}] {message}")


def validate_config(config: AgentLocalConfig, for_registration: bool = True) -> Tuple[bool, List[str]]:
    """
    校验配置合法性

    Args:
        config: Agent 本地配置
        for_registration: True=注册前校验（检查必填字段），False=运行时校验

    Returns:
        (is_valid, errors)
    """
    errors = []

    # ── 必填字段 ──
    if for_registration:
        if not config.agent_name:
            errors.append("agent_name 不能为空")
        elif len(config.agent_name) > 32:
            errors.append(f"agent_name 过长（{len(config.agent_name)}/32）")

        if not config.framework:
            errors.append("framework 不能为空")
        elif len(config.framework) > 64:
            errors.append(f"framework 过长（{len(config.framework)}/64）")

        if not config.operator_id:
            errors.append("operator_id 不能为空（需先向 Hub 管理员申请）")

    # ── 注册后校验 ──
    if not for_registration:
        if not config.agent_id:
            errors.append("agent_id 为空（未注册）")
        if not config.agent_secret:
            errors.append("agent_secret 为空（未注册）")

    # ── Channel 校验 ──
    for ch in config.channels:
        if ch not in CHANNEL_WHITELIST and not ch.startswith("ext:"):
            errors.append(f"不支持的 channel: {ch}")

    # ── Commands 校验 ──
    if config.commands and not isinstance(config.commands, dict):
        errors.append("commands 必须是字典格式")
    if config.commands and "chat" not in config.commands:
        errors.append("commands 必须包含 chat 命令模板")

    # ── 数值范围 ──
    if config.timeout < 10 or config.timeout > 600:
        errors.append(f"timeout 不合理: {config.timeout}（建议 10-600 秒）")

    if config.hub_port < 1 or config.hub_port > 65535:
        errors.append(f"hub_port 不合法: {config.hub_port}")

    return len(errors) == 0, errors


def validate_for_registration(config: AgentLocalConfig) -> None:
    """注册前校验，失败抛异常"""
    ok, errors = validate_config(config, for_registration=True)
    if not ok:
        raise ConfigValidationError("multiple", "; ".join(errors))


# ── 文件 I/O ─────────────────────────

def load_config(path: Optional[Path] = None) -> AgentLocalConfig:
    """加载本地配置文件"""
    if path is None:
        path = DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE
    path = Path(path)

    if not path.exists():
        return AgentLocalConfig()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return AgentLocalConfig.from_dict(data)


def save_config(config: AgentLocalConfig, path: Optional[Path] = None) -> None:
    """保存本地配置文件"""
    if path is None:
        path = DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)

    # 设置权限（含 secret，仅 owner 可读写）
    os.chmod(path, 0o600)


def create_template(
    agent_name: str,
    framework: str,
    operator_id: str,
    emoji: str = "🤖",
    hub_host: str = "127.0.0.1",
    hub_port: int = 18900,
) -> AgentLocalConfig:
    """
    快速创建配置模板

    用法：
        config = create_template("我的Agent", "openclaw", "OP0001")
        save_config(config, Path("./my_agent_config.json"))
    """
    # 根据框架自动填充 commands
    commands = _default_commands(framework)

    return AgentLocalConfig(
        agent_name=agent_name,
        emoji=emoji,
        framework=framework,
        operator_id=operator_id,
        hub_host=hub_host,
        hub_port=hub_port,
        commands=commands,
    )


def _default_commands(framework: str) -> dict:
    """根据框架生成默认命令模板"""
    templates = {
        "openclaw": {
            "chat": "openclaw chat --agent {agent_id} --msg '{message}'",
            "health": "openclaw status",
        },
        "hermes": {
            "chat": "hermes send --to {agent_id} --msg '{message}'",
            "health": "hermes health",
        },
        "crewai": {
            "chat": "crewai run --agent {agent_id} --input '{message}'",
            "health": "crewai status",
        },
    }
    return templates.get(framework, {
        "chat": f"{framework} chat --agent {{agent_id}} --msg '{{message}}'",
    })


# ── 便捷函数 ─────────────────────────

def get_or_create_config(path: Optional[Path] = None) -> AgentLocalConfig:
    """获取配置，不存在则返回空模板"""
    if path is None:
        path = DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE
    if Path(path).exists():
        return load_config(path)
    return AgentLocalConfig()


def print_config_summary(config: AgentLocalConfig) -> str:
    """打印配置摘要（隐藏 secret）"""
    lines = [
        f"📋 Agent 配置摘要",
        f"  名称: {config.emoji} {config.agent_name}",
        f"  框架: {config.framework}",
        f"  操作人: {config.operator_id}",
        f"  Hub: {config.server_url}",
        f"  状态: {'✅ 已注册' if config.is_registered else '⏳ 未注册'}",
    ]
    if config.is_registered:
        lines.append(f"  Agent ID: {config.agent_id}")
        lines.append(f"  Secret: {config.agent_secret[:12]}...（已隐藏）")
    if config.capabilities:
        caps = config.capabilities
        if "提供" in caps:
            lines.append(f"  提供: {', '.join(caps['提供'])}")
        if "需求" in caps:
            lines.append(f"  需求: {', '.join(caps['需求'])}")
    return "\n".join(lines)
