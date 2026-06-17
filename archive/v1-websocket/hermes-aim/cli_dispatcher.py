#!/usr/bin/env python3
"""
P3: CLI 抽象层调度器

核心功能：
1. 命令模板解析与变量替换
2. 跨框架 Agent 统一调度
3. 健康检查与超时控制
4. 安全防护（命令注入防护）

设计文档：~/shared/aim/aim-cli-abstraction.md

用法：
    from cli_dispatcher import CLIDispatcher

    dispatcher = CLIDispatcher(registry)
    result = dispatcher.dispatch_chat("ZS0002", "ZS0001", "你好")
"""

import asyncio
import logging
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Any

# 同目录导入
import sys
sys.path.insert(0, str(Path(__file__).parent))
from registry import AgentRegistry, RegisteredAgent


# ── 数据结构 ─────────────────────────

@dataclass
class DispatchResult:
    """调度结果"""
    success: bool
    agent_id: str
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_ms: int = 0
    error: str = ""
    timeout: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "agent_id": self.agent_id,
            "command": self.command,
            "stdout": self.stdout[:1000] if self.stdout else "",  # 截断过长输出
            "stderr": self.stderr[:500] if self.stderr else "",
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "timeout": self.timeout,
        }


@dataclass
class HealthCheckResult:
    """健康检查结果"""
    agent_id: str
    healthy: bool
    message: str = ""
    duration_ms: int = 0
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "healthy": self.healthy,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "checked_at": self.checked_at,
        }


# ── 调度器 ─────────────────────────

class CLIDispatcher:
    """
    CLI 调度器 — 跨框架统一调度

    通过 Agent 注册时声明的 commands 模板，实现：
    - chat: 发送消息给 Agent
    - health: 健康检查
    - task: 分配任务
    - 自定义命令：按需扩展
    """

    # 默认超时（秒）
    DEFAULT_TIMEOUT = 120
    # 健康检查超时（秒）
    HEALTH_TIMEOUT = 10
    # 最大输出长度（字节）
    MAX_OUTPUT_BYTES = 1024 * 1024  # 1MB

    def __init__(self, registry: AgentRegistry, log: logging.Logger = None):
        self.registry = registry
        self.log = log or logging.getLogger("aim.cli_dispatcher")

        # 调度统计
        self._stats: Dict[str, Dict[str, int]] = {}  # {agent_id: {cmd: count}}

    def dispatch_chat(
        self,
        from_agent: str,
        to_agent: str,
        message: str,
        timeout: Optional[int] = None,
    ) -> DispatchResult:
        """
        发送消息给目标 Agent

        Args:
            from_agent: 发送方 Agent ID
            to_agent: 目标 Agent ID
            message: 消息内容
            timeout: 超时秒数（可选，覆盖 Agent 配置）

        Returns:
            DispatchResult
        """
        return self._dispatch(
            from_agent=from_agent,
            to_agent=to_agent,
            command_key="chat",
            variables={"msg": message, "from": from_agent, "to": to_agent},
            timeout=timeout,
        )

    def dispatch_task(
        self,
        from_agent: str,
        to_agent: str,
        task_id: str,
        description: str,
        timeout: Optional[int] = None,
    ) -> DispatchResult:
        """
        分配任务给目标 Agent

        Args:
            from_agent: 发送方 Agent ID
            to_agent: 目标 Agent ID
            task_id: 任务 ID
            description: 任务描述
            timeout: 超时秒数

        Returns:
            DispatchResult
        """
        return self._dispatch(
            from_agent=from_agent,
            to_agent=to_agent,
            command_key="task",
            variables={
                "task_id": task_id,
                "desc": description,
                "from": from_agent,
                "to": to_agent,
            },
            timeout=timeout,
        )

    def dispatch_health_check(self, agent_id: str) -> HealthCheckResult:
        """
        执行健康检查

        Args:
            agent_id: 目标 Agent ID

        Returns:
            HealthCheckResult
        """
        agent = self.registry.get(agent_id)
        if not agent:
            return HealthCheckResult(
                agent_id=agent_id,
                healthy=False,
                message=f"Agent {agent_id} 不存在",
            )

        if not agent.is_active:
            return HealthCheckResult(
                agent_id=agent_id,
                healthy=False,
                message=f"Agent {agent_id} 不在线",
            )

        # 检查是否有 health 命令
        health_cmd = agent.commands.get("health")
        if not health_cmd:
            # 无 health 命令，视为健康（跳过检查）
            return HealthCheckResult(
                agent_id=agent_id,
                healthy=True,
                message="无 health 命令，跳过检查",
            )

        # 变量替换
        variables = {
            "port": str(agent.port) if agent.port else "",
            "host": agent.host or "127.0.0.1",
            "cli_path": agent.cli_path or "",
        }

        try:
            cmd = self._resolve_template(health_cmd, variables)
        except ValueError as e:
            return HealthCheckResult(
                agent_id=agent_id,
                healthy=False,
                message=f"模板解析失败: {e}",
            )

        # 执行
        start_ms = int(time.time() * 1000)
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.HEALTH_TIMEOUT,
            )
            duration_ms = int(time.time() * 1000) - start_ms

            healthy = result.returncode == 0
            message = result.stdout.strip() if healthy else result.stderr.strip()

            return HealthCheckResult(
                agent_id=agent_id,
                healthy=healthy,
                message=message[:200],  # 截断
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired:
            return HealthCheckResult(
                agent_id=agent_id,
                healthy=False,
                message=f"健康检查超时 ({self.HEALTH_TIMEOUT}s)",
                duration_ms=self.HEALTH_TIMEOUT * 1000,
            )
        except Exception as e:
            return HealthCheckResult(
                agent_id=agent_id,
                healthy=False,
                message=f"执行异常: {e}",
            )

    def dispatch_custom(
        self,
        from_agent: str,
        to_agent: str,
        command_key: str,
        variables: Dict[str, str],
        timeout: Optional[int] = None,
    ) -> DispatchResult:
        """
        执行自定义命令

        Args:
            from_agent: 发送方 Agent ID
            to_agent: 目标 Agent ID
            command_key: 命令 key（如 "deploy", "restart"）
            variables: 模板变量
            timeout: 超时秒数

        Returns:
            DispatchResult
        """
        return self._dispatch(
            from_agent=from_agent,
            to_agent=to_agent,
            command_key=command_key,
            variables=variables,
            timeout=timeout,
        )

    def batch_health_check(self, agent_ids: list = None) -> Dict[str, HealthCheckResult]:
        """
        批量健康检查

        Args:
            agent_ids: 目标 Agent ID 列表（None=检查所有活跃 Agent）

        Returns:
            {agent_id: HealthCheckResult}
        """
        if agent_ids is None:
            agent_ids = [a.agent_id for a in self.registry.list_active()]

        results = {}
        for agent_id in agent_ids:
            results[agent_id] = self.dispatch_health_check(agent_id)

        return results

    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """获取调度统计"""
        return dict(self._stats)

    # ── 内部方法 ─────────────────────

    def _dispatch(
        self,
        from_agent: str,
        to_agent: str,
        command_key: str,
        variables: Dict[str, str],
        timeout: Optional[int] = None,
    ) -> DispatchResult:
        """执行调度"""
        # 1. 查注册表
        agent = self.registry.get(to_agent)
        if not agent:
            return DispatchResult(
                success=False,
                agent_id=to_agent,
                command=command_key,
                error=f"Agent {to_agent} 不存在",
            )

        if not agent.is_active:
            return DispatchResult(
                success=False,
                agent_id=to_agent,
                command=command_key,
                error=f"Agent {to_agent} 不在线",
            )

        # 2. 获取命令模板
        cmd_template = agent.commands.get(command_key)
        if not cmd_template:
            return DispatchResult(
                success=False,
                agent_id=to_agent,
                command=command_key,
                error=f"Agent {to_agent} 未声明 {command_key} 命令",
            )

        # 3. 补充通用变量
        variables.setdefault("cli_path", agent.cli_path or "")
        variables.setdefault("port", str(agent.port) if agent.port else "")
        variables.setdefault("host", agent.host or "127.0.0.1")
        variables.setdefault("timeout", str(timeout or agent.timeout or self.DEFAULT_TIMEOUT))

        # 4. 模板变量替换
        try:
            cmd = self._resolve_template(cmd_template, variables)
        except ValueError as e:
            return DispatchResult(
                success=False,
                agent_id=to_agent,
                command=command_key,
                error=f"模板解析失败: {e}",
            )

        # 5. 安全检查
        security_error = self._security_check(cmd, agent)
        if security_error:
            return DispatchResult(
                success=False,
                agent_id=to_agent,
                command=cmd,
                error=security_error,
            )

        # 6. 执行命令
        effective_timeout = timeout or agent.timeout or self.DEFAULT_TIMEOUT
        start_ms = int(time.time() * 1000)

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            duration_ms = int(time.time() * 1000) - start_ms

            # 记录统计
            self._record_stat(to_agent, command_key)

            return DispatchResult(
                success=result.returncode == 0,
                agent_id=to_agent,
                command=cmd,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired:
            return DispatchResult(
                success=False,
                agent_id=to_agent,
                command=cmd,
                error=f"命令超时 ({effective_timeout}s)",
                timeout=True,
                duration_ms=effective_timeout * 1000,
            )
        except Exception as e:
            return DispatchResult(
                success=False,
                agent_id=to_agent,
                command=cmd,
                error=f"执行异常: {e}",
            )

    def _resolve_template(self, template: str, variables: Dict[str, str]) -> str:
        """
        解析命令模板，替换变量

        变量格式：{var_name}

        Args:
            template: 命令模板
            variables: 变量字典

        Returns:
            替换后的命令字符串

        Raises:
            ValueError: 缺少必要变量
        """
        # 检查是否有未定义的变量
        import re
        placeholders = re.findall(r'\{(\w+)\}', template)
        missing = [p for p in placeholders if p not in variables]
        if missing:
            raise ValueError(f"缺少变量: {', '.join(missing)}")

        # 替换变量（对字符串值做 shlex.quote 防注入）
        safe_vars = {}
        for k, v in variables.items():
            if isinstance(v, str):
                # 对消息内容做 shell 转义
                safe_vars[k] = shlex.quote(v)
            else:
                safe_vars[k] = str(v)

        return template.format(**safe_vars)

    def _security_check(self, cmd: str, agent: RegisteredAgent) -> Optional[str]:
        """
        安全检查

        Args:
            cmd: 待执行命令
            agent: 目标 Agent

        Returns:
            None=通过, str=错误信息
        """
        # 1. 检查 cli_path 白名单（如果配置了）
        # TODO: 从配置读取白名单目录

        # 2. 检查危险命令
        dangerous_patterns = [
            'rm -rf /',
            'mkfs',
            'dd if=',
            ':(){:|:&};:',  # fork bomb
            'chmod 777 /',
        ]
        cmd_lower = cmd.lower()
        for pattern in dangerous_patterns:
            if pattern in cmd_lower:
                return f"危险命令被拦截: 包含 '{pattern}'"

        # 3. 检查命令长度
        if len(cmd) > 4096:
            return f"命令过长: {len(cmd)} > 4096"

        return None

    def _record_stat(self, agent_id: str, command_key: str):
        """记录调度统计"""
        if agent_id not in self._stats:
            self._stats[agent_id] = {}
        self._stats[agent_id][command_key] = self._stats[agent_id].get(command_key, 0) + 1


# ── 便捷函数 ─────────────────────────

def create_dispatcher(registry: AgentRegistry) -> CLIDispatcher:
    """创建调度器实例"""
    return CLIDispatcher(registry)


# ── 测试入口 ─────────────────────────

def main():
    """测试调度器"""
    import json
    from registry import OperatorRegistry

    # 初始化注册表
    op_registry = OperatorRegistry()
    agent_registry = AgentRegistry(op_registry)

    # 加载配置
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)

        # 预置种子 Agent
        if "agents" in config:
            for agent_id, info in config["agents"].items():
                agent_registry.add_seed(
                    agent_id=agent_id,
                    operator_id="OP0001",
                    agent_name=info.get("name", agent_id),
                    emoji=info.get("emoji", "🤖"),
                    framework=info.get("framework", ""),
                )

    # 创建调度器
    dispatcher = CLIDispatcher(agent_registry)

    # 测试健康检查
    print("=== 健康检查测试 ===")
    for agent in agent_registry.list_active():
        result = dispatcher.dispatch_health_check(agent.agent_id)
        status = "✅" if result.healthy else "❌"
        print(f"{status} {agent.agent_id}: {result.message}")

    # 测试 chat 调度（模拟）
    print("\n=== Chat 调度测试 ===")
    agents = agent_registry.list_active()
    if len(agents) >= 2:
        from_agent = agents[0].agent_id
        to_agent = agents[1].agent_id
        result = dispatcher.dispatch_chat(from_agent, to_agent, "测试消息")
        print(f"从 {from_agent} 到 {to_agent}: {'✅' if result.success else '❌'}")
        if result.error:
            print(f"  错误: {result.error}")

    # 打印统计
    print("\n=== 调度统计 ===")
    print(json.dumps(dispatcher.get_stats(), indent=2))


if __name__ == "__main__":
    main()
