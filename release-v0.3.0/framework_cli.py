"""
FrameworkCLI — 框架 CLI 统一调用器

用命令模板替代 aim-agent.py 中的 if/elif 硬编码框架路由。
新框架接入只需在 config.json 的 commands 段加一个模板，不改代码。

Phase 1: 继承 CLIAdapter，统一使用 AIRequest/AIResponse

Author: 呱呱 🐸 | Review: 吉量 🐴
"""

import os
import json
import asyncio
import logging
import shlex
import time
from typing import Optional

from cli_adapter import CLIAdapter
from ai_types import AIRequest, AIResponse

log = logging.getLogger("aim.cli")


class FrameworkCLI(CLIAdapter):
    """框架 CLI 统一调用器"""

    def __init__(self, framework: str, commands: dict, cli_paths: dict):
        self.framework = framework
        self.commands = commands or {}
        self.cli_paths = cli_paths or {}
        self._chat_config = self.commands.get(framework, {}).get("chat")
        self.cmd_config = self._chat_config  # 默认用基础模板

    @property
    def framework_name(self) -> str:
        """框架名称"""
        return self.framework

    async def call(self, request: AIRequest) -> AIResponse:
        """统一调用接口（CLIAdapter 实现）

        Args:
            request: AIRequest 数据类

        Returns:
            AIResponse 数据类
        """
        start_time = time.monotonic()

        prompt = request.prompt
        timeout = request.effective_timeout()
        session_id = request.session_id
        agent_id = request.agent_id
        session_key = request.session_key

        # 选择模板：有 session 参数时用 cmd_with_session，否则用基础 cmd
        if self._chat_config:
            if (session_id or session_key) and "cmd_with_session" in self._chat_config:
                self.cmd_config = dict(self._chat_config)
                self.cmd_config["cmd"] = self._chat_config["cmd_with_session"]
            else:
                self.cmd_config = self._chat_config

        if not self.cmd_config:
            # 降级：无 commands 配置时，用 cli_paths + 默认模板
            return await self._fallback_call(request)

        timeout = timeout or self.cmd_config.get("timeout", 120)
        cmd = self._build_cmd(prompt, timeout, session_id, agent_id, session_key)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            latency_ms = int((time.monotonic() - start_time) * 1000)

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()[:300]
                return AIResponse(
                    success=False,
                    error=f"退出码 {proc.returncode}: {err}",
                    latency_ms=latency_ms,
                )

            text, extracted_session = self._parse_output(stdout)
            return AIResponse(
                success=True,
                text=text,
                session_id=extracted_session,
                latency_ms=latency_ms,
            )

        except asyncio.TimeoutError:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            return AIResponse(
                success=False,
                error=f"超时 ({timeout}秒)",
                latency_ms=latency_ms,
            )
        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            return AIResponse(
                success=False,
                error=str(e),
                latency_ms=latency_ms,
            )

    def _build_cmd(self, prompt: str, timeout: int,
                   session_id: str, agent_id: str,
                   session_key: str = None) -> list:
        """构建命令行（v1.1: 所有用户输入经 shlex.quote 防注入）"""
        variables = {
            "cli": self.cli_paths.get(self.framework, self.framework),
            "prompt": shlex.quote(prompt),          # P0: 防 shell 注入
            "timeout": str(timeout),
            "session_id": shlex.quote(session_id or ""),  # P0: 防注入
            "agent_id": shlex.quote(agent_id or ""),      # P0: 防注入
            "session_key": shlex.quote(session_key or ""),  # openclaw delegate 用
        }
        # 模板内的额外变量（如 python312, script）
        for k, v in self.cmd_config.items():
            if k in ("python312", "script", "python"):
                variables[k] = os.path.expanduser(str(v))

        cmd = []
        for part in self.cmd_config["cmd"]:
            for var_name, var_value in variables.items():
                part = part.replace(f"{{{var_name}}}", var_value)
            cmd.append(part)
        return cmd

    def _parse_output(self, stdout: bytes) -> tuple:
        """解析输出（v1.1: 返回 (text, extracted_session_id)）"""
        output = stdout.decode("utf-8").strip()
        extracted_session = None

        # 逐行处理：先提取 session_id，再过滤
        lines = output.split("\n")
        clean_lines = []
        filters = self.cmd_config.get("filter", [])

        for line in lines:
            # 提取 session_id（hermes: "session_id:xxx", qwenpaw: "[SESSION:xxx]"）
            if line.startswith("session_id:"):
                sid = line.split(":", 1)[1].strip()
                if sid:
                    extracted_session = sid
                continue  # session 行不输出
            if line.startswith("[SESSION:"):
                sid = line.replace("[SESSION:", "").replace("]", "").strip()
                if sid:
                    extracted_session = sid
                continue
            # 其他 filter
            if not any(f in line for f in filters):
                clean_lines.append(line)

        text = "\n".join(clean_lines).strip()

        # JSON 提取
        if self.cmd_config.get("output") == "json":
            try:
                data = json.loads(text)
                path = self.cmd_config.get("json_path", "")
                text = self._extract_json(data, path)
            except json.JSONDecodeError:
                pass

        return text, extracted_session

    def _extract_json(self, data, path: str) -> str:
        """从 JSON 中提取文本

        支持路径: "result.payloads.0.text"
        """
        if not path:
            return str(data)

        parts = path.split(".")
        current = data
        for part in parts:
            if current is None:
                return ""
            if isinstance(current, list):
                try:
                    current = current[int(part)]
                except (IndexError, ValueError):
                    return ""
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return str(current)
        return str(current) if current is not None else ""

    async def _fallback_call(self, request: AIRequest) -> AIResponse:
        """降级调用：无 commands 配置时，用 cli_paths + 硬编码默认模板

        向后兼容旧 config.json（没有 commands 段）
        """
        start_time = time.monotonic()
        prompt = request.prompt
        timeout = request.effective_timeout()
        session_id = request.session_id
        agent_id = request.agent_id

        cli = self.cli_paths.get(self.framework)
        if not cli:
            return AIResponse(success=False, error=f"未配置 {self.framework} 的 CLI 路径")

        cli = os.path.expanduser(cli)
        timeout = timeout or 120

        # 默认模板
        if self.framework == "hermes":
            cmd = [cli, "chat", "-q", prompt, "-Q"]
        elif self.framework == "openclaw":
            cmd = [cli, "agent", "--agent", "main", "-m", prompt, "--json"]
        elif self.framework == "qwenpaw":
            cmd = [cli, "agent", "chat", "--from-agent", "default",
                   "--to-agent", "default", "--text", prompt,
                   "--timeout", str(timeout)]
        elif self.framework == "crewai":
            # crewai 需要特殊处理（Python 3.12 子进程）
            crewai_py = os.path.expanduser(
                "~/.local/share/uv/tools/crewai/bin/python"
            )
            script = os.path.expanduser("~/.hermes/aim/crewai_llm_call.py")
            cmd = [crewai_py, script, prompt, str(timeout)]
        elif self.framework == "callback":
            # 回调脚本模式 — 子进程调用 handler.sh
            #
            # 协议：
            #   argv[1] = 发送方 ID（如 ZS0001）
            #   stdin   = 完整 AI 提示词（含"收到来自 X 的消息：..."）
            #   stdout  = 回复内容（空字符串 = 不回复）
            #   退出码 0 = 成功，非 0 = 失败
            #
            # handler.sh 路径查找优先级：
            #   1. cli_paths.callback（config.json 中配置）
            #   2. ~/.aim/agent-{agent_id}/handler.sh（按 agent_id 自动拼）
            handler_path = self.cli_paths.get("callback")
            if not handler_path:
                agent_id = request.agent_id
                if not agent_id:
                    return AIResponse(success=False, error="callback 模式需要 agent_id")
                handler_path = os.path.expanduser(f"~/.aim/agent-{agent_id}/handler.sh")

            if not os.path.exists(handler_path):
                return AIResponse(success=False, error=f"handler.sh 不存在: {handler_path}")

            if not os.access(handler_path, os.X_OK):
                return AIResponse(success=False, error=f"handler.sh 不可执行: {handler_path}")

            # 构建参数脚本（规避 shlex.quote 拆散中文/特殊字符）
            # shlex.quote 在提示词内容上会加单引号，导致 bash 收到 '中文内容'
            # 用 heredoc stdin 传递消息内容，argv 只传递发送方 ID
            sender = request.agent_id or "unknown"
            cmd = [handler_path, sender]
            stdin_data = prompt.encode("utf-8", errors="replace")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data), timeout=timeout
            )

            latency_ms = int((time.monotonic() - start_time) * 1000)

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()[:300]
                return AIResponse(
                    success=False,
                    error=f"handler.sh 退出码 {proc.returncode}: {err}",
                    latency_ms=latency_ms,
                )

            text = stdout.decode("utf-8", errors="replace").strip()
            return AIResponse(success=True, text=text, latency_ms=latency_ms)

        else:
            return AIResponse(success=False, error=f"未知框架: {self.framework}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            latency_ms = int((time.monotonic() - start_time) * 1000)

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()[:300]
                return AIResponse(
                    success=False,
                    error=f"退出码 {proc.returncode}: {err}",
                    latency_ms=latency_ms,
                )

            text = stdout.decode("utf-8").strip()
            return AIResponse(success=True, text=text, latency_ms=latency_ms)

        except asyncio.TimeoutError:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            return AIResponse(success=False, error=f"超时 ({timeout}秒)", latency_ms=latency_ms)
        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            return AIResponse(success=False, error=str(e), latency_ms=latency_ms)
