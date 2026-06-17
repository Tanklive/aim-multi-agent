"""
AIM Agent 注册流程 CLI

提供命令行界面，让新 Agent 向 Hub 注册。

用法：
    # 1. 创建配置模板
    python register_agent.py init --name "我的Agent" --framework openclaw --operator OP0001

    # 2. 编辑配置（可选）
    vim ~/.hermes/aim/agent_config.json

    # 3. 校验配置
    python register_agent.py validate

    # 4. 注册到 Hub
    python register_agent.py register

    # 5. 查看状态
    python register_agent.py status

    # 6. 测试认证
    python register_agent.py auth-test

设计文档：~/shared/aim/AIM-AGENT-REGISTRATION.md
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

# 同目录导入
sys.path.insert(0, str(Path(__file__).parent))
from agent_config import (
    AgentLocalConfig, load_config, save_config, create_template,
    validate_config, print_config_summary, DEFAULT_CONFIG_DIR,
    DEFAULT_CONFIG_FILE, ConfigValidationError,
)


# ── 注册客户端 ─────────────────────

class RegistrationClient:
    """
    注册客户端 — 负责与 Hub 通信完成注册

    当前版本：本地模拟（直接调用 registry.py）
    后续版本：通过 WebSocket/HTTP 调用 Hub API
    """

    def __init__(self, config: AgentLocalConfig):
        self.config = config

    def register(self, dry_run: bool = False) -> dict:
        """
        执行注册流程

        Args:
            dry_run: True=仅校验不实际注册

        Returns:
            {
                "success": bool,
                "agent_id": str,
                "agent_secret": str,
                "reason": str,
                "failed_check": str,
            }
        """
        # Step 1: 校验本地配置
        ok, errors = validate_config(self.config, for_registration=True)
        if not ok:
            return {
                "success": False,
                "reason": f"本地配置校验失败: {'; '.join(errors)}",
                "failed_check": "local_validation",
            }

        if dry_run:
            return {"success": True, "reason": "dry_run 通过，配置合法"}

        # Step 2: 构造注册请求
        request_data = self._build_register_request()

        # Step 3: 发送到 Hub（当前本地模拟，后续走网络）
        result = self._send_register_request(request_data)

        # Step 4: 注册成功 → 更新本地配置
        if result.get("success"):
            self.config.agent_id = result["agent_id"]
            self.config.agent_secret = result["agent_secret"]
            self.config.registered_at = time.time()
            save_config(self.config)
            result["config_saved"] = True

        return result

    def authenticate(self) -> dict:
        """
        测试 HMAC 认证

        用本地 agent_id + agent_secret 生成签名，发送给 Hub 验证
        """
        if not self.config.is_registered:
            return {"success": False, "reason": "未注册，无法认证"}

        timestamp = int(time.time())
        message = f"{self.config.agent_id}:{timestamp}"

        # 本地生成签名
        secret_hash = hashlib.sha256(self.config.agent_secret.encode()).hexdigest()
        signature = hmac.new(
            secret_hash.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        # 发送到 Hub 验证（当前本地模拟）
        return self._send_auth_request(self.config.agent_id, signature, timestamp)

    def _build_register_request(self) -> dict:
        """构造注册请求数据"""
        return {
            "agent_name": self.config.agent_name,
            "emoji": self.config.emoji,
            "framework": self.config.framework,
            "version": self.config.version,
            "operator_id": self.config.operator_id,
            "capabilities": self.config.capabilities,
            "cli_path": self.config.cli_path,
            "commands": self.config.commands,
            "timeout": self.config.timeout,
            "channels": self.config.channels,
            "handler": self.config.handler,
        }

    def _send_register_request(self, request_data: dict) -> dict:
        """
        发送注册请求到 Hub

        当前：本地直接调用 registry.py
        后续：WebSocket /register 命令
        """
        try:
            from registry import OperatorRegistry, AgentRegistry, RegisterRequest

            # 加载 Hub 配置
            hub_config_path = DEFAULT_CONFIG_DIR / "config.json"
            if not hub_config_path.exists():
                return {"success": False, "reason": "Hub 配置不存在，请先初始化 Hub"}

            with open(hub_config_path) as f:
                hub_config = json.load(f)

            # 初始化注册表
            op_registry = OperatorRegistry()
            if "operators" in hub_config:
                op_registry.load_from_config(hub_config["operators"])

            agent_registry = AgentRegistry(op_registry)

            # 预置种子 Agent
            if "agents" in hub_config:
                for agent_id, agent_info in hub_config["agents"].items():
                    agent_registry.add_seed(
                        agent_id=agent_id,
                        operator_id="OP0001",  # 种子默认归属大哥
                        agent_name=agent_info.get("name", agent_id),
                        emoji=agent_info.get("emoji", "🤖"),
                        framework=agent_info.get("framework", ""),
                    )

            # 构造 RegisterRequest
            req = RegisterRequest.from_dict(request_data)

            # 执行注册
            result = agent_registry.register(req, client_ip="127.0.0.1")

            return {
                "success": result.success,
                "agent_id": result.agent_id,
                "agent_secret": result.agent_secret,
                "reason": result.reason,
                "failed_check": result.failed_check,
                "operator_id": result.operator_id,
                "agents_remaining": result.agents_remaining,
            }

        except Exception as e:
            return {"success": False, "reason": f"注册异常: {e}"}

    def _send_auth_request(self, agent_id: str, signature: str, timestamp: int) -> dict:
        """发送认证请求到 Hub"""
        try:
            from registry import OperatorRegistry, AgentRegistry

            hub_config_path = DEFAULT_CONFIG_DIR / "config.json"
            with open(hub_config_path) as f:
                hub_config = json.load(f)

            op_registry = OperatorRegistry()
            if "operators" in hub_config:
                op_registry.load_from_config(hub_config["operators"])

            agent_registry = AgentRegistry(op_registry)

            # 预置种子 + 已注册 Agent
            if "agents" in hub_config:
                for aid, info in hub_config["agents"].items():
                    agent_registry.add_seed(
                        agent_id=aid,
                        operator_id="OP0001",
                        agent_name=info.get("name", aid),
                        emoji=info.get("emoji", "🤖"),
                        framework=info.get("framework", ""),
                    )

            # 尝试认证
            ok, msg = agent_registry.authenticate(agent_id, signature, timestamp)
            return {"success": ok, "reason": msg}

        except Exception as e:
            return {"success": False, "reason": f"认证异常: {e}"}


# ── CLI 命令 ─────────────────────────

def cmd_init(args):
    """创建配置模板"""
    config_path = Path(args.output) if args.output else DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE

    if config_path.exists() and not args.force:
        print(f"❌ 配置文件已存在: {config_path}")
        print(f"   使用 --force 覆盖")
        return 1

    config = create_template(
        agent_name=args.name,
        framework=args.framework,
        operator_id=args.operator,
        emoji=args.emoji,
        hub_host=args.hub_host,
        hub_port=args.hub_port,
    )

    save_config(config, config_path)
    print(f"✅ 配置模板已创建: {config_path}")
    print()
    print(print_config_summary(config))
    print()
    print("📝 下一步:")
    print(f"   1. 编辑配置: vim {config_path}")
    print(f"   2. 校验配置: python register_agent.py validate")
    print(f"   3. 注册到 Hub: python register_agent.py register")
    return 0


def cmd_validate(args):
    """校验配置"""
    config = load_config()

    if not config.agent_name:
        print("❌ 配置文件为空或不存在")
        print(f"   先创建模板: python register_agent.py init --name 'xxx' --framework openclaw --operator OP0001")
        return 1

    print(print_config_summary(config))
    print()

    ok, errors = validate_config(config, for_registration=not config.is_registered)

    if ok:
        print("✅ 配置校验通过")
        if config.is_registered:
            print("   已注册，可直接使用")
        else:
            print("   未注册，可执行 register 命令")
        return 0
    else:
        print("❌ 配置校验失败:")
        for err in errors:
            print(f"   • {err}")
        return 1


def cmd_register(args):
    """注册到 Hub"""
    config = load_config()

    if not config.agent_name:
        print("❌ 配置文件为空，请先 init")
        return 1

    if config.is_registered and not args.force:
        print(f"⚠️  已注册为 {config.agent_id}，使用 --force 重新注册")
        return 1

    print("🔄 正在注册...")
    print(f"   Hub: {config.server_url}")
    print(f"   Agent: {config.emoji} {config.agent_name} ({config.framework})")
    print()

    client = RegistrationClient(config)

    if args.dry_run:
        result = client.register(dry_run=True)
        print(f"🔍 Dry-run 结果: {'✅ 通过' if result['success'] else '❌ 失败'}")
        if not result["success"]:
            print(f"   原因: {result['reason']}")
        return 0 if result["success"] else 1

    result = client.register()

    if result["success"]:
        print(f"✅ 注册成功!")
        print(f"   Agent ID: {result['agent_id']}")
        print(f"   Secret: {result['agent_secret'][:12]}...（已保存到本地配置）")
        print(f"   操作人剩余配额: {result.get('agents_remaining', '?')}")
        print()
        print("📝 下一步:")
        print(f"   1. 测试认证: python register_agent.py auth-test")
        print(f"   2. 连接 Hub: python register_agent.py connect")
        return 0
    else:
        print(f"❌ 注册失败")
        print(f"   标准: {result.get('failed_check', '?')}")
        print(f"   原因: {result['reason']}")
        return 1


def cmd_auth_test(args):
    """测试 HMAC 认证"""
    config = load_config()

    if not config.is_registered:
        print("❌ 未注册，无法测试认证")
        return 1

    print(f"🔑 测试认证: {config.agent_id}")
    client = RegistrationClient(config)
    result = client.authenticate()

    if result["success"]:
        print(f"✅ 认证通过!")
        return 0
    else:
        print(f"❌ 认证失败: {result['reason']}")
        return 1


def cmd_status(args):
    """查看配置状态"""
    config = load_config()

    if not config.agent_name:
        print("❌ 配置文件为空")
        return 1

    print(print_config_summary(config))
    print()
    print(f"📁 配置路径: {DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE}")
    return 0


def cmd_template(args):
    """生成示例配置（stdout）"""
    config = create_template(
        agent_name="示例Agent",
        framework="openclaw",
        operator_id="OP0001",
    )
    print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
    return 0


# ── CLI 入口 ─────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AIM Agent 注册工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s init --name "我的Agent" --framework openclaw --operator OP0001
  %(prog)s validate
  %(prog)s register
  %(prog)s register --dry-run
  %(prog)s auth-test
  %(prog)s status
  %(prog)s template
        """,
    )

    sub = parser.add_subparsers(dest="command", help="子命令")

    # init
    p_init = sub.add_parser("init", help="创建配置模板")
    p_init.add_argument("--name", required=True, help="Agent 名称")
    p_init.add_argument("--framework", required=True, help="框架（openclaw/hermes/crewai/...）")
    p_init.add_argument("--operator", required=True, help="操作人 ID（如 OP0001）")
    p_init.add_argument("--emoji", default="🤖", help="Emoji 标识")
    p_init.add_argument("--hub-host", default="127.0.0.1", help="Hub 地址")
    p_init.add_argument("--hub-port", type=int, default=18900, help="Hub 端口")
    p_init.add_argument("--output", "-o", help="输出路径（默认 ~/.hermes/aim/agent_config.json）")
    p_init.add_argument("--force", action="store_true", help="覆盖已有配置")

    # validate
    sub.add_parser("validate", help="校验配置")

    # register
    p_reg = sub.add_parser("register", help="注册到 Hub")
    p_reg.add_argument("--dry-run", action="store_true", help="仅校验不实际注册")
    p_reg.add_argument("--force", action="store_true", help="强制重新注册")

    # auth-test
    sub.add_parser("auth-test", help="测试 HMAC 认证")

    # status
    sub.add_parser("status", help="查看配置状态")

    # template
    sub.add_parser("template", help="生成示例配置 JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    cmd_map = {
        "init": cmd_init,
        "validate": cmd_validate,
        "register": cmd_register,
        "auth-test": cmd_auth_test,
        "status": cmd_status,
        "template": cmd_template,
    }

    return cmd_map[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
